# Copyright (c) 2013 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.
"""Test Auth."""

import falcon
from falcon import testing
from keystoneclient.middleware import auth_token

from marconi.tests.transport.wsgi import base


class TestWSGIAuth(base.TestBase):

    config_filename = 'keystone_auth.conf'

    def setUp(self):
        super(TestWSGIAuth, self).setUp()
        self.headers = {'Client-ID': '30387f00'}

    def test_auth_install(self):
        self.assertTrue(isinstance(self.app,
                                   auth_token.AuthProtocol))

    def test_non_authenticated(self):
        env = testing.create_environ('/v1/480924/queues/',
                                     method='GET',
                                     headers=self.headers)

        self.app(env, self.srmock)
        self.assertEquals(self.srmock.status, falcon.HTTP_401)
