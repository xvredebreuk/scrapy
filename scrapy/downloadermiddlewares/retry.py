"""
An extension to retry failed requests that are potentially caused by temporary
problems such as a connection timeout or HTTP 500 error.

You can change the behaviour of this middleware by modifing the scraping settings:
RETRY_TIMES - how many times to retry a failed page
RETRY_HTTP_CODES - which HTTP response codes to retry

Failed pages are collected on the scraping process and rescheduled at the end,
once the spider has finished crawling all regular (non failed) pages. Once
there is no more failed pages to retry this middleware sends a signal
(retry_complete), so other extensions could connect to that signal.
"""
import logging
import warnings

import six
from scrapy.exceptions import NotConfigured, ScrapyDeprecationWarning
from scrapy.utils.response import response_status_message
from scrapy.utils.python import global_object_name
from scrapy.utils.misc import load_object
from scrapy.settings import Settings

logger = logging.getLogger(__name__)

class BackwardsCompatibilityMetaclass(type):
    @property
    def EXCEPTIONS_TO_RETRY(self):
        warnings.warn("Attribute RetryMiddleware.EXCEPTIONS_TO_RETRY is deprecated. "
        "Use the RETRY_EXCEPTIONS setting instead.",
                      ScrapyDeprecationWarning, stacklevel=2)
        return tuple(
                load_object(x) if isinstance(x, six.string_types) else x
                for x in Settings().getlist('RETRY_EXCEPTIONS')
            )

class RetryMiddleware(six.with_metaclass(BackwardsCompatibilityMetaclass, object)):

    def __init__(self, settings):
        if not settings.getbool('RETRY_ENABLED'):
            raise NotConfigured
        self.max_retry_times = settings.getint('RETRY_TIMES')
        self.retry_http_codes = set(int(x) for x in settings.getlist('RETRY_HTTP_CODES'))
        self.priority_adjust = settings.getint('RETRY_PRIORITY_ADJUST')
        if not hasattr(self, "EXCEPTIONS_TO_RETRY"): # If EXCEPTIONS_TO_RETRY is not "overriden"
            self.exceptions_to_retry = tuple(
                load_object(x) if isinstance(x, six.string_types) else x
                for x in settings.getlist('RETRY_EXCEPTIONS')
            )
        else:
            self.exceptions_to_retry = self.EXCEPTIONS_TO_RETRY

    @classmethod
    def from_crawler(cls, crawler):
        return cls(crawler.settings)

    def process_response(self, request, response, spider):
        if request.meta.get('dont_retry', False):
            return response
        if response.status in self.retry_http_codes:
            reason = response_status_message(response.status)
            return self._retry(request, reason, spider) or response
        return response

    def process_exception(self, request, exception, spider):
        if isinstance(exception, self.exceptions_to_retry) \
                and not request.meta.get('dont_retry', False):
            return self._retry(request, exception, spider)

    def _retry(self, request, reason, spider):
        retries = request.meta.get('retry_times', 0) + 1

        retry_times = self.max_retry_times

        if 'max_retry_times' in request.meta:
            retry_times = request.meta['max_retry_times']

        stats = spider.crawler.stats
        if retries <= retry_times:
            logger.debug("Retrying %(request)s (failed %(retries)d times): %(reason)s",
                         {'request': request, 'retries': retries, 'reason': reason},
                         extra={'spider': spider})
            retryreq = request.copy()
            retryreq.meta['retry_times'] = retries
            retryreq.dont_filter = True
            retryreq.priority = request.priority + self.priority_adjust

            if isinstance(reason, Exception):
                reason = global_object_name(reason.__class__)

            stats.inc_value('retry/count')
            stats.inc_value('retry/reason_count/%s' % reason)
            return retryreq
        else:
            stats.inc_value('retry/max_reached')
            logger.debug("Gave up retrying %(request)s (failed %(retries)d times): %(reason)s",
                         {'request': request, 'retries': retries, 'reason': reason},
                         extra={'spider': spider})
