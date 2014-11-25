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

from oslo.config import cfg
from stevedore import driver

from zaqar import common
from zaqar.common import decorators
from zaqar.i18n import _
from zaqar.openstack.common import log as logging
from zaqar.notifications.storage import base

LOG = logging.getLogger(__name__)

_PIPELINE_RESOURCES = ('subscription',)

_PIPELINE_CONFIGS = tuple((
    cfg.ListOpt(resource + '_pipeline', default=[],
                help=_('Pipeline to use for processing {0} operations. '
                       'This pipeline will be consumed before calling '
                       'the storage driver\'s controller methods.')
                .format(resource))
    for resource in _PIPELINE_RESOURCES
))

_PIPELINE_GROUP = 'storage'


def _config_options():
    return [(_PIPELINE_GROUP, _PIPELINE_CONFIGS)]


def _get_storage_pipeline(resource_name, conf):
    """Constructs and returns a storage resource pipeline.

    This is a helper function for any service supporting
    pipelines for the storage layer. The function returns
    a pipeline based on the `{resource_name}_pipeline`
    config option.

    Stages in the pipeline implement controller methods
    that they want to hook. A stage can halt the
    pipeline immediate by returning a value that is
    not None; otherwise, processing will continue
    to the next stage, ending with the actual storage
    controller.

    :param conf: Configuration instance.
    :type conf: `cfg.ConfigOpts`

    :returns: A pipeline to use.
    :rtype: `Pipeline`
    """
    conf.register_opts(_PIPELINE_CONFIGS,
                       group=_PIPELINE_GROUP)

    storage_conf = conf[_PIPELINE_GROUP]

    pipeline = []
    for ns in storage_conf[resource_name + '_pipeline']:
        try:
            mgr = driver.DriverManager('zaqar.notifications.storage.stages',
                                       ns, invoke_on_load=True)
            pipeline.append(mgr.driver)
        except RuntimeError as exc:
            LOG.warning(_(u'Stage %(stage)d could not be imported: %(ex)s'),
                        {'stage': ns, 'ex': str(exc)})
            continue

    return common.Pipeline(pipeline)


class DataDriver(base.DataDriverBase):
    """Meta-driver for injecting pipelines in front of controllers.

    :param conf: Configuration from which to load pipeline settings
    :param storage: Storage driver that will service requests as the
        last step in the pipeline
    """

    def __init__(self, conf, storage):
        # NOTE(kgriffs): Pass None for cache since it won't ever
        # be referenced.
        super(DataDriver, self).__init__(conf, None)
        self._storage = storage

    def is_alive(self):
        return self._storage.is_alive()

    def _health(self):
        return self._storage._health()

    @decorators.lazy_property(write=False)
    def subscription_controller(self):
        stages = _get_storage_pipeline('subscription', self.conf)
        stages.append(self._storage.subscription_controller)
        return stages
