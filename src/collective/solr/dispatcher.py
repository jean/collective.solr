from zope.interface import implements
from zope.component import queryUtility, queryMultiAdapter, getSiteManager
from zope.publisher.interfaces.http import IHTTPRequest
from Acquisition import aq_base
from Products.ZCatalog.ZCatalog import ZCatalog

from collective.solr.interfaces import ISolrConnectionConfig
from collective.solr.interfaces import ISearchDispatcher
from collective.solr.interfaces import ISearch
from collective.solr.interfaces import IFlare
from collective.solr.utils import isActive, prepareData
from collective.solr.mangler import mangleQuery
from collective.solr.mangler import extractQueryParameters
from collective.solr.mangler import cleanupQueryParameters
from collective.solr.mangler import optimizeQueryParameters
from collective.solr.lingua import languageFilter

from collective.solr.monkey import patchCatalogTool, patchLazyCat
patchCatalogTool()      # patch catalog tool to use the dispatcher...
patchLazyCat()          # ...as well as ZCatalog's Lazy class

from collective.solr.attributes import registerAttributes
registerAttributes()    # register additional indexable attributes


class FallBackException(Exception):
    """ exception indicating the dispatcher should fall back to searching
        the portal catalog """


class SearchDispatcher(object):
    """ adapter for potentially dispatching a given query to an
        alternative search backend (instead of the portal catalog) """
    implements(ISearchDispatcher)

    def __init__(self, context):
        self.context = context

    def __call__(self, request, **keywords):
        """ decide on a search backend and perform the given query """
        if isActive():
            try:
                return solrSearchResults(request, **keywords)
            except FallBackException:
                pass
        if getattr(aq_base(self.context), '_cs_old_searchResults', None):
            return self.context._cs_old_searchResults(request, **keywords)
        return ZCatalog.searchResults(self.context, request, **keywords)


def solrSearchResults(request=None, **keywords):
    """ perform a query using solr after translating the passed in
        parameters with portal catalog semantics """
    search = queryUtility(ISearch)
    config = queryUtility(ISolrConnectionConfig)
    if request is None:
        # try to get a request instance, so that flares can be adapted to
        # ploneflares and urls can be converted into absolute ones etc;
        # however, in this case any arguments from the request are ignored
        request = getattr(getSiteManager(), 'REQUEST', None)
        args = keywords
    elif IHTTPRequest.providedBy(request):
        args = request.form.copy()  # ignore headers and other stuff
        args.update(keywords)       # keywords take precedence
    else:
        assert isinstance(request, dict), request
        args = request.copy()
        args.update(keywords)       # keywords take precedence
        # if request is a dict, we need the real request in order to
        # be able to adapt to plone flares
        request = getattr(getSiteManager(), 'REQUEST', args)
    if config.required:
        required = set(config.required).intersection(args)
        if required:
            for key in required:
                if not key in args:
                    raise FallBackException
        else:
            raise FallBackException
    schema = search.getManager().getSchema() or {}
    params = cleanupQueryParameters(extractQueryParameters(args), schema)
    languageFilter(args)
    mangleQuery(args)
    prepareData(args)
    query = search.buildQuery(**args)
    optimizeQueryParameters(query, params)
    __traceback_info__ = (query, params, args)
    response = search(query, fl='* score', **params)
    def wrap(flare):
        """ wrap a flare object with a helper class """
        adapter = queryMultiAdapter((flare, request), IFlare)
        return adapter is not None and adapter or flare
    results = response.results()
    for idx, flare in enumerate(results):
        results[idx] = wrap(flare)
    return response