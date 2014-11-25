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

"""Mongodb storage driver implementation."""

import ssl

import pymongo
import pymongo.errors

from zaqar.common import decorators
from zaqar.i18n import _
from zaqar.openstack.common import log as logging
from zaqar.notifications import storage
from zaqar.notifications.storage.mongodb import controllers
from zaqar.notifications.storage.mongodb import options


LOG = logging.getLogger(__name__)


def _connection(conf):
    if conf.uri and 'replicaSet' in conf.uri:
        MongoClient = pymongo.MongoReplicaSetClient
    else:
        MongoClient = pymongo.MongoClient

    if conf.uri and 'ssl=true' in conf.uri.lower():
        kwargs = {}

        # Default to CERT_REQUIRED
        ssl_cert_reqs = ssl.CERT_REQUIRED

        if conf.ssl_cert_reqs == 'CERT_OPTIONAL':
            ssl_cert_reqs = ssl.CERT_OPTIONAL

        if conf.ssl_cert_reqs == 'CERT_NONE':
            ssl_cert_reqs = ssl.CERT_NONE

        kwargs['ssl_cert_reqs'] = ssl_cert_reqs

        if conf.ssl_keyfile:
            kwargs['ssl_keyfile'] = conf.ssl_keyfile
        if conf.ssl_certfile:
            kwargs['ssl_certfile'] = conf.ssl_certfile
        if conf.ssl_ca_certs:
            kwargs['ssl_ca_certs'] = conf.ssl_ca_certs

        return MongoClient(conf.uri, **kwargs)

    return MongoClient(conf.uri)


class DataDriver(storage.DataDriverBase):

    _DRIVER_OPTIONS = options._config_options()

    def __init__(self, conf, cache):
        super(DataDriver, self).__init__(conf, cache)

        self.mongodb_conf = self.conf[options.MONGODB_GROUP]

        conn = self.connection
        server_version = conn.server_info()['version']

        if tuple(map(int, server_version.split('.'))) < (2, 2):
            raise RuntimeError(_('The mongodb driver requires mongodb>=2.2,  '
                                 '%s found') % server_version)

        if not len(conn.nodes) > 1 and not conn.is_mongos:
            if not self.conf.unreliable:
                raise RuntimeError(_('Either a replica set or a mongos is '
                                     'required to guarantee message delivery'))
        else:
            wc = conn.write_concern.get('w')
            majority = (wc == 'majority' or
                        wc >= 2)

            if not wc:
                # NOTE(flaper87): No write concern specified, use majority
                # and don't count journal as a replica. Use `update` to avoid
                # overwriting `wtimeout`
                conn.write_concern.update({'w': 'majority'})
            elif not self.conf.unreliable and not majority:
                raise RuntimeError(_('Using a write concern other than '
                                     '`majority` or > 2 makes the service '
                                     'unreliable. Please use a different '
                                     'write concern or set `unreliable` '
                                     'to True in the config file.'))

            conn.write_concern['j'] = False

    def is_alive(self):
        pass

    def _health(self):
        pass

    @decorators.lazy_property(write=False)
    def subscriptions_database(self):
        """Database dedicated to the "queues" collection.

        The queues collection is separated out into its own database
        to avoid writer lock contention with the messages collections.
        """

        name = self.mongodb_conf.database + '_subscriptions'
        return self.connection[name]

    @decorators.lazy_property(write=False)
    def connection(self):
        """MongoDB client connection instance."""
        return _connection(self.mongodb_conf)

    @decorators.lazy_property(write=False)
    def subscription_controller(self):
        return controllers.SubscrptionController(self)
