# Copyright (c) 2013 Red Hat, Inc.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
# http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from zaqar.common import api


class RequestSchema(api.Api):

    schema = {
        'subscription_list': {
            'ref': 'subscriptions',
            'method': 'GET',
            'properties': {
                'marker': {'type': 'string'},
                'limit': {'type': 'integer'},
                'detailed': {'type': 'boolean'}
            }
        },

        'subscription_create': {
            'ref': 'subscriptions',
            'method': 'PUT',
        },

        'subscription_delete': {
            'ref': 'subscriptions/{subscription_id}',
            'method': 'DELETE',
            'required': ['subscription_id'],
            'properties': {
                'subscription_id': {'type': 'string'}
            }
        },

        'subscription_get': {
            'ref': 'subscriptions/{subscription_id}',
            'method': 'GET',
            'required': ['subscription_id'],
            'properties': {
                'subscription_id': {'type': 'string'}
            }
        },
    }
