# Copyright (c) 2013 Rackspace, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may not
# use this file except in compliance with the License.  You may obtain a copy
# of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations under
# the License.

import json


# NOTE(kgriffs): http://tools.ietf.org/html/draft-nottingham-json-home-03
JSON_HOME = {
    'resources': {
        # -----------------------------------------------------------------
        # Subscriptions
        # -----------------------------------------------------------------
        'rel/subscriptions': {
            'href-template': '/v2.0/subscriptions{?marker,limit,detailed}',
            'href-vars': {
                'marker': 'param/marker',
                'limit': 'param/subscription_limit',
                'detailed': 'param/detailed',
            },
            'hints': {
                'allow': ['GET'],
                'formats': {
                    'application/json': {},
                },
            },
        },
        'rel/subscription': {
            'href-template': '/v2.0/subscriptions/{subscription_id}',
            'href-vars': {
                'queue_name': 'param/subscription_id',
            },
            'hints': {
                'allow': ['PUT', 'DELETE'],
                'formats': {
                    'application/json': {},
                },
            },
        },
    }
}


class Resource(object):

    def __init__(self):
        document = json.dumps(JSON_HOME, ensure_ascii=False, indent=4)
        self.document_utf8 = document.encode('utf-8')

    def on_get(self, req, resp, project_id):
        resp.data = self.document_utf8

        resp.content_type = 'application/json-home'
        resp.cache_control = ['max-age=86400']
        # status defaults to 200
