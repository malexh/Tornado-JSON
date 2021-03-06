import sys
import json

from tornado.testing import AsyncHTTPTestCase

from .utils import handle_import_error

try:
    sys.path.append('.')
    from tornado_json import routes
    from tornado_json import schema
    from tornado_json import application
    from tornado_json import requesthandlers
    sys.path.append('demos/helloworld')
    import helloworld
except ImportError as err:
    handle_import_error(err)


def jd(obj):
    return json.dumps(obj)


def jl(s):
    return json.loads(s.decode("utf-8"))


class DummyView(requesthandlers.ViewHandler):
    """Dummy ViewHandler for coverage"""
    def delete(self):
        # Reference db_conn to test for AttributeError
        self.db_conn


class DBTestHandler(requesthandlers.APIHandler):
    """APIHandler for testing db_conn"""
    def get(self):
        # Set application.db_conn to test if db_conn BaseHandler
        #   property works
        self.application.db_conn = {"data": "Nothing to see here."}
        self.success(self.db_conn.get("data"))


class ExplodingHandler(requesthandlers.APIHandler):

    @schema.validate(**{
        "input_schema": None,
        "output_schema": {
            "type": "number",
        }
    })
    def get(self):
        """This handler is used for testing purposes and is explosive."""
        return "I am not the handler you are looking for."

    @schema.validate(**{
        "input_schema": {
            "type": "number",
        },
        "output_schema": {
            "type": "number",
        }
    })
    def post(self):
        """This handler is used for testing purposes and is explosive."""
        return "Fission mailed."


class APIFunctionalTest(AsyncHTTPTestCase):

    def get_app(self):
        rts = routes.get_routes(helloworld)
        rts += [
            ("/api/explodinghandler", ExplodingHandler),
            ("/views/someview", DummyView),
            ("/api/dbtest", DBTestHandler)
        ]
        return application.Application(
            routes=rts,
            settings={"debug": True},
            db_conn=None
        )

    def test_synchronous_handler(self):
        r = self.fetch(
            "/api/helloworld"
        )
        self.assertEqual(r.code, 200)
        self.assertEqual(
            jl(r.body)["data"],
            "Hello world!"
        )

    def test_asynchronous_handler(self):
        r = self.fetch(
            "/api/asynchelloworld"
        )
        self.assertEqual(r.code, 200)
        self.assertEqual(
            jl(r.body)["data"],
            "Hello (asynchronous) world!"
        )

    def test_post_request(self):
        r = self.fetch(
            "/api/postit",
            method="POST",
            body=jd({
                "title": "Very Important Post-It Note",
                "body": "Equally important message",
                "index": 0
            })
        )
        self.assertEqual(r.code, 200)
        self.assertEqual(
            jl(r.body)["data"]["message"],
            "Very Important Post-It Note was posted."
        )

    def test_url_pattern_route(self):
        r = self.fetch(
            "/api/greeting/John/Smith"
        )
        self.assertEqual(r.code, 200)
        self.assertEqual(
            jl(r.body)["data"],
            "Greetings, John Smith!"
        )

    def test_write_error(self):
        # Test malformed output
        r = self.fetch(
            "/api/explodinghandler"
        )
        self.assertEqual(r.code, 500)
        self.assertEqual(
            jl(r.body)["status"],
            "error"
        )
        # Test malformed input
        r = self.fetch(
            "/api/explodinghandler",
            method="POST",
            body='"Yup", "this is going to end badly."]'
        )
        self.assertEqual(r.code, 400)
        self.assertEqual(
            jl(r.body)["status"],
            "fail"
        )

    def test_view_db_conn(self):
        r = self.fetch(
            "/views/someview",
            method="DELETE"
        )
        self.assertEqual(r.code, 500)
        self.assertTrue(
            "No database connection was provided." in r.body.decode("UTF-8")
        )

    def test_db_conn(self):
        r = self.fetch(
            "/api/dbtest",
            method="GET"
        )
        self.assertEqual(r.code, 200)
        print(r.body)
        self.assertEqual(
            jl(r.body)["status"],
            "success"
        )
        self.assertTrue(
            "Nothing to see here." in jl(r.body)["data"]
        )
