import json
import urllib
import tornado
import peewee_async
from models.base import db
from tornado.testing import AsyncTestCase, AsyncHTTPTestCase
from main import make_app
from tornado import gen, testing
from models.user import User

class TornadoMethodTest(AsyncHTTPTestCase):
    def get_new_ioloop(self):  # override this method
        return tornado.platform.asyncio.AsyncIOMainLoop()

    def prepare(self):
        u = User.get_or_none(username='test')
        if u:
            u.delete_instance()

    def get_app(self):
        app = make_app()
        app.objects = peewee_async.Manager(db)
        return app

    def test_home(self):
        response = self.fetch('/')
        self.assertEqual(response.code, 200)

    def test_register_user_correct(self):
        self.prepare()
        body = urllib.parse.urlencode({
            'username': 'test',
            'password': 'password',
            'password_confirmation': 'password',
            'email': 'testemail@test.com'
        })

        response = self.fetch('/api/v1/register', 
                                method='POST', 
                                body=body)
        self.assertEqual(response.code, 200)

    def test_register_user_username_exists(self):
        body = urllib.parse.urlencode({
            'username': 'test',
            'password': 'password',
            'password_confirmation': 'password',
            'email': 'testemail@test.com'
        })

        response = self.fetch('/api/v1/register', 
                                method='POST', 
                                body=body)
        self.assertEqual(response.code, 400) 

    def test_register_user_bad_email(self):
        self.prepare()
        body = urllib.parse.urlencode({
            'username': 'test',
            'password': 'password',
            'password_confirmation': 'password',
            'email': 'testemailtest.com'
        })

        response = self.fetch('/api/v1/register', 
                                method='POST', 
                                body=body)
        self.assertEqual(response.code, 500) 