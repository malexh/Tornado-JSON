import pyclbr
import pkgutil
import importlib
import inspect
from itertools import chain
from functools import reduce
from functools import partial
from collections import namedtuple

from tornado_json.constants import HTTP_METHODS, basestring
from tornado_json.utils import extract_method, is_method, is_handler_subclass
from tornado_json.utils import ensure_endswith


AutoURL = namedtuple('AutoURL', ['type'])


def get_routes(package):
    """
    This will walk ``package`` and generates routes from any and all
    ``APIHandler`` and ``ViewHandler`` subclasses it finds. If you need to
    customize or remove any routes, you can do so to the list of
    returned routes that this generates.

    :type  package: package
    :param package: The package containing RequestHandlers to generate
        routes from
    :returns: List of routes for all submodules of ``package``
    :rtype: [(url, RequestHandler), ... ]
    """
    return list(chain(*[get_module_routes(modname) for modname in
                        gen_submodule_names(package)]))


def gen_submodule_names(package):
    """Walk package and yield names of all submodules

    :type  package: package
    :param package: The package to get submodule names of
    :returns: Iterator that yields names of all submodules of ``package``
    :rtype: Iterator that yields ``str``
    """
    for importer, modname, ispkg in pkgutil.walk_packages(
        path=package.__path__,
        prefix=package.__name__ + '.',
            onerror=lambda x: None):
        yield modname


def get_module_routes(module_name, custom_routes=None, exclusions=None):
    """Create and return routes for module_name

    Routes are (url, RequestHandler) tuples

    :returns: list of routes for ``module_name`` with respect to ``exclusions``
        and ``custom_routes``. Returned routes are with URLs formatted such
        that they are forward-slash-separated by module/class level
        and end with the lowercase name of the RequestHandler (it will also
        remove 'handler' from the end of the name of the handler).
        For example, a requesthandler with the name
        ``helloworld.api.HelloWorldHandler`` would be assigned the url
        ``/api/helloworld``.
        Additionally, if a method has extra arguments aside from ``self`` in
        its signature, routes with URL patterns will be generated to
        match ``r"(?P<{}>[a-zA-Z0-9_]+)".format(argname)`` for each
        argument. The aforementioned regex will match ONLY values
        with alphanumeric+underscore characters.
    :rtype: [(url, RequestHandler), ... ]
    :type  module_name: str
    :param module_name: Name of the module to get routes for
    :type  custom_routes: [(str, RequestHandler), ... ]
    :param custom_routes: List of routes that have custom URLs and therefore
        should be automagically generated
    :type  exclusions: [str, str, ...]
    :param exclusions: List of RequestHandler names that routes should not be
        generated for
    """
    def has_method(module, cls_name, method_name):
        return all([
            method_name in vars(getattr(module, cls_name)),
            is_method(reduce(getattr, [module, cls_name, method_name]))
        ])

    def yield_args(module, cls_name, method_name):
        """Get signature of ``module.cls_name.method_name``

        Confession: This function doesn't actually ``yield`` the arguments,
            just returns a list. Trust me, it's better that way.

        :returns: List of arg names from method_name except ``self``
        :rtype: list
        """
        wrapped_method = reduce(getattr, [module, cls_name, method_name])
        method = extract_method(wrapped_method)
        return [a for a in inspect.getargspec(method).args if a not in ["self"]]

    def generate_auto_routes(module, module_name, cls_name, method_name):
        """Generate auto routes based on handler attributes

        :rtype: generator
        :returns: Generator that yields automatic routes
        """
        handler = getattr(module, cls_name)
        gen_route = partial(generate_route, module, module_name, cls_name,
                            method_name)
        if handler._tj_no_auto_route is False:
            yield gen_route(url_name=AutoURL('self'))
        if handler._tj_route_base is True:
            yield gen_route(url_name=AutoURL('module'))

    def generate_route(module, module_name, cls_name, method_name, url_name):
        """Generate URL for current context given ``url_name``

        :rtype: str
        :returns: Constructed URL based on given arguments
        """
        def get_handler_name():
            """Get handler identifier for URL

            For the special case where ``url_name`` is:

            * ``AutoURL("self")``, the handler is named a lowercase
            value of its own name with 'handler' removed
            from the ending if given
            * ``AutoURL("module")``, return ``""``

            ...otherwise, we simply use the provided ``url_name``
            """
            if url_name == AutoURL('self'):
                if cls_name.lower().endswith('handler'):
                    res = cls_name.lower().replace('handler', '', 1)
                else:
                    res = cls_name.lower()
            elif url_name == AutoURL('module'):
                res = None
            else:
                res = url_name

            return "/{}".format(res) if res is not None else ""

        def get_arg_route():
            """Get remainder of URL determined by method argspec

            :returns: Remainder of URL which matches `\w+` regex
                with groups named by the method's argument spec.
                If there are no arguments given, returns ``""``.
            :rtype: str
            """
            if yield_args(module, cls_name, method_name):
                return "/{}/?$".format("/".join(
                    ["(?P<{}>[a-zA-Z0-9_]+)".format(argname) for argname
                     in yield_args(module, cls_name, method_name)]
                ))
            return r"/?"

        def get_module_path():
            """Get module path joined together with ``"/"``"""
            return "/{path}".format(path="/".join(module_name.split(".")[1:]))

        return "{}{}{}".format(
            get_module_path(),
            get_handler_name(),
            get_arg_route()
        )

    if not custom_routes:
        custom_routes = []
    if not exclusions:
        exclusions = []

    # Import module so we can get its request handlers
    module = importlib.import_module(module_name)

    # Generate list of RequestHandler names in custom_routes
    custom_routes_s = [c.__name__ for r, c in custom_routes]

    # rhs is a dict of {classname: pyclbr.Class} key, value pairs
    rhs = pyclbr.readmodule(module_name)

    # You better believe this is a list comprehension
    auto_routes = list(chain(*[
        list(set(chain(*[
            # Generate a route for each "end_pattern" specified in the
            # by the decorator this requesthandler
            [
                # URL, requesthandler tuple
                (
                    generate_route(
                        module, module_name, cls_name, method_name, url_name
                    ),
                    getattr(module, cls_name)
                ) for url_name in getattr(module, cls_name)._tj_end_pattern
            # Add routes for each custom URL specified as a "pattern"
            # by the ``route`` decorator if one decorates this requesthandler
            ] + [
                (
                    url,
                    getattr(module, cls_name)
                ) for url in getattr(module, cls_name)._tj_pattern
            # Generate auto routes as set by other attributes from
            # the ``route`` decorator
            ] + list(generate_auto_routes(module, module_name,
                                          cls_name, method_name))
            # We create a route for each HTTP method in the handler
            #   so that we catch all possible routes if different
            #   HTTP methods have different argspecs and are expecting
            #   to catch different routes. Any duplicate routes
            #   are removed from the set() comparison.
            for method_name in HTTP_METHODS if has_method(
                module, cls_name, method_name)
        ])))
        # foreach classname, pyclbr.Class in rhs
        for cls_name, cls in rhs.items()
        # Only add the pair to auto_routes if:
        #    * the superclass is in the list of supers we want
        #    * the requesthandler isn't already paired in custom_routes
        #    * the requesthandler isn't manually excluded
        if is_handler_subclass(cls)
        and cls_name not in (custom_routes_s + exclusions)
    ]))

    routes = auto_routes + custom_routes
    return routes


"""Idea for Issue #45

For the special handlers that Misaka42 wanted (which make a lot of sense)
to have, can have special decorators which denote the routes for those.

This allows for naming flexibility and makes it "less magic" for the user.

These decorators would be based off of a more general ``route`` decorator (yes,
borrowing its name from Flask.route). This route decorator would be a replacement
for the current rather unsightly implementation of URL Annotations which makes
the user assign class level __url_names__ and __urls__ themselves.
"""


def route(pattern=None, end_pattern=None, no_auto_route=True):
    """Decorator for customized mapping of routes to a RequestHandler.

    This only adds attributes to the handlers indicating to
    :func:`get_routes` how to generate request routes.
    Usage of :func:`get_routes` to generate routes and pass to the
    application is still required as per the documentation to get your
    Tornado application to actually recognize route requests to handlers.

    The following examples all route two URLs:
    "/api/helloworld/helloworld/?$", and "/api/helloworld/foobar/?$" to the
    mock ``HelloWorld`` handler.

        @route(end_pattern="foobar", keep_auto_route=True)
        class HelloWorld(RequestHandler):
            return "Hello World"

        @route(end_pattern="helloworld", pattern=["/api/helloworld/foobar"])
        class HelloWorldHandler(RequestHandler):
            return "foobar"

        @route(end_pattern=["helloworld", "foobar"])
        class HelloWorldHandler(RequestHandler):
            return "foobar"

    :type pattern: str|list
    :param pattern: This can be a single, or a list of, entire URL patterns,
                    to map the handler being decorated.
    :type end_pattern: str|list
    :param end_pattern: Setting this sets the final part of the URL, i.e., what
                  would usually be set by the handler name. Example:
                  if you were to decorate a handler
                  ``api.helloworld.HelloWorld`` with ``end_pattern="foobar"``,
                  the route mapped to the handler would be
                  ``"/api/helloworld/foobar"``. This can also be set as a list
                  of names which would map multiple routes to the handler, e.g.,
                  ``end_pattern=["foo", "bar", "baz"]``
    :type no_auto_route: bool
    :param no_auto_route: If this is set to ``False``, the automatically
                          generated route that would be generated for
                          the handler being decorated by this, is kept
                          along with the additional routes.
    """
    def _sanitize_pattern(p):
        p = p.rstrip("$")
        p = ensure_endswith(p, "/?")
        return "{p}$".format(p=p)

    def _transform_attr(attr):
        if isinstance(attr, basestring):
            return [attr]
        elif isinstance(attr, (tuple, list)):
            return list(attr)
        elif attr is None:
            return []
        else:
            raise TypeError("Unsupported type {} for {}; expected `str` "
                            "or `list`)".format(type(attr).__name__, attr))

    def _route(handler):
        handler._tj_end_pattern = _transform_attr(end_pattern)
        handler._tj_pattern = map(_sanitize_pattern, _transform_attr(pattern))
        handler._tj_no_auto_route = no_auto_route
        return handler
    return _route


def baseroute(handler):
    """Route requests for the current module's path to the handler decorated
    with this.

    This allows you to have handlers handle base paths for resources, e.g.,
    ``handlers.images.ImagesHandler -> /api/images`` or,
    ``handlers.images.ImageHandler -> /api/images/(.*)`` if that is the
    specified pattern for the argspec of an HTTP method in your handler.

    You can leave additional handlers, such as
    ``handlers.images.PopularHandler -> /api/images/popular``
    to be automatically handled as expected by the routing system.
    """
    handler._tj_route_base = True
    handler._tj_no_auto_route = True
    return handler
