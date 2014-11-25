# Copyright (c) 2013 Rackspace, Inc.
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

from oslo.config import cfg
from stevedore import driver

from zaqar.common import decorators
from zaqar.common import errors
from zaqar.i18n import _
from zaqar.openstack.common.cache import cache as oslo_cache
from zaqar.openstack.common import log
from zaqar.notifications.storage import pipeline as notifications_pipeline
from zaqar.queues.storage import pipeline
from zaqar.queues.storage import pooling
from zaqar.common import storage_utils

LOG = log.getLogger(__name__)


ADMIN_MODE_OPT = cfg.BoolOpt('admin_mode', default=False,
                             help='Activate privileged endpoints.')

_CLI_OPTIONS = (
    ADMIN_MODE_OPT,
    cfg.BoolOpt('daemon', default=False,
                help='Run Zaqar server in the background.'),
)

# NOTE (Obulpathi): Register daemon command line option for
# zaqar-server
CONF = cfg.CONF
CONF.register_cli_opts(_CLI_OPTIONS)

_GENERAL_OPTIONS = (
    ADMIN_MODE_OPT,
    cfg.BoolOpt('pooling', default=False,
                help=('Enable pooling across multiple storage backends. '
                      'If pooling is enabled, the storage driver '
                      'configuration is used to determine where the '
                      'catalogue/control plane data is kept.'),
                deprecated_opts=[cfg.DeprecatedOpt('sharding')]),
    cfg.BoolOpt('unreliable', default=None,
                help=('Disable all reliability constrains.')),
)

_DRIVER_OPTIONS = (
    cfg.StrOpt('transport', default='wsgi',
               help='Transport driver to use.'),
    cfg.StrOpt('storage', default='sqlalchemy',
               help='Storage driver to use.'),
)

_DRIVER_GROUP = 'drivers'


def _config_options():
    return [(None, _GENERAL_OPTIONS),
            (_DRIVER_GROUP, _DRIVER_OPTIONS)]


class Bootstrap(object):
    """Defines the Zaqar bootstrapper.

    The bootstrap loads up drivers per a given configuration, and
    manages their lifetimes.
    """

    def __init__(self, conf):
        self.conf = conf
        self.conf.register_opts(_GENERAL_OPTIONS)
        self.conf.register_opts(_DRIVER_OPTIONS, group=_DRIVER_GROUP)
        self.driver_conf = self.conf[_DRIVER_GROUP]

        log.setup('zaqar')

        if self.conf.unreliable is None:
            msg = _(u'Unreliable\'s default value will be changed to False '
                    'in the Kilo release. Please, make sure your deployments '
                    'are working in a reliable mode or that `unreliable` is '
                    'explicitly set to `True` in your configuration files.')
            LOG.warn(msg)
            self.conf.unreliable = True

    @decorators.lazy_property(write=False)
    def queues_storage(self):
        LOG.debug(u'Loading storage driver')

        if self.conf.pooling:
            LOG.debug(u'Storage pooling enabled')
            storage_driver = pooling.DataDriver(self.conf, self.cache,
                                                self.control,
                                                service_type='queues')
        else:
            storage_driver = storage_utils.load_storage_driver(
                self.conf, self.cache)

        LOG.debug(u'Loading storage pipeline')
        return pipeline.DataDriver(self.conf, storage_driver)

    @decorators.lazy_property(write=False)
    def notifications_storage(self):
        LOG.debug(u'Loading storage driver')

        if self.conf.pooling:
            LOG.debug(u'Storage pooling enabled')
            storage_driver = pooling.DataDriver(self.conf, self.cache,
                                                self.control,
                                                service_type='notifications')
        else:
            storage_driver = storage_utils.load_storage_driver(
                self.conf, self.cache, service_type='notifications')

        LOG.debug(u'Loading storage pipeline')
        return notifications_pipeline.DataDriver(self.conf, storage_driver)

    @decorators.lazy_property(write=False)
    def control(self):
        LOG.debug(u'Loading storage control driver')
        return storage_utils.load_storage_driver(self.conf, self.cache,
                                                 control_mode=True)

    @decorators.lazy_property(write=False)
    def cache(self):
        LOG.debug(u'Loading proxy cache driver')
        try:
            oslo_cache.register_oslo_configs(self.conf)
            mgr = oslo_cache.get_cache(self.conf.cache_url)
            return mgr
        except RuntimeError as exc:
            LOG.exception(exc)
            raise errors.InvalidDriver(exc)

    @decorators.lazy_property(write=False)
<<<<<<< HEAD:zaqar/bootstrap.py
    def queues_transport(self):
=======
    def base_transport(self):
>>>>>>> e7dbe1feebc09cb33344999f064439d16571b349:zaqar/bootstrap.py
        transport_name = self.driver_conf.transport
        LOG.debug(u'Loading transport driver: %s', transport_name)

        args = [
            self.conf,
            self.queues_storage,
            self.cache,
            self.control,
        ]
        try:
            mgr = driver.DriverManager('zaqar.queues.transport',
                                       transport_name,
                                       invoke_on_load=True,
                                       invoke_args=args)
            return mgr.driver
        except RuntimeError as exc:
            LOG.exception(exc)
            raise errors.InvalidDriver(exc)

    @decorators.lazy_property(write=False)
    def notifications_transport(self):
        transport_name = self.driver_conf.transport
        LOG.debug(u'Loading transport driver: %s', transport_name)

        args = [
            self.conf,
            self.notifications_storage,
            self.cache,
            self.control,
        ]
        try:
            mgr = driver.DriverManager('zaqar.notifications.transport',
                                       transport_name,
                                       invoke_on_load=True,
                                       invoke_args=args)
            return mgr.driver
        except RuntimeError as exc:
            LOG.exception(exc)
            raise errors.InvalidDriver(exc)

    def run(self):
        base_transport = self.queues_transport

        for version_path, endpoints in self.notifications_transport.catalog:
            for route, resource in endpoints:
                base_transport.app._app.add_route(version_path + route,
                                                  resource)

        base_transport.listen()
