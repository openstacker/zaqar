# Copyright (c) 2013 Rackspace, Inc.
# Copyright 2014 Catalyst IT Ltd
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

import heapq
import itertools

from oslo.config import cfg

from zaqar.common import decorators
from zaqar.common.storage import select
from zaqar.openstack.common import log
from zaqar.queues import storage
from zaqar.queues.storage import errors
from zaqar.common import storage_utils

LOG = log.getLogger(__name__)

_CATALOG_OPTIONS = (
    cfg.StrOpt('storage', default='sqlalchemy',
               help='Catalog storage driver.'),
)

_CATALOG_GROUP = 'pooling:catalog'

# NOTE(kgriffs): E.g.: 'zaqar-pooling:5083853/my-queue'
_POOL_CACHE_PREFIX = 'pooling:'

# TODO(kgriffs): If a queue is migrated, everyone's
# caches need to have the relevant entry invalidated
# before "unfreezing" the queue, rather than waiting
# on the TTL.
#
# TODO(kgriffs): Make configurable?
_POOL_CACHE_TTL = 10


def _config_options():
    return [(_CATALOG_GROUP, _CATALOG_OPTIONS)]


def _pool_cache_key(queue, project=None):
    # NOTE(kgriffs): Use string concatenation for performance,
    # also put project first since it is guaranteed to be
    # unique, which should reduce lookup time.
    return _POOL_CACHE_PREFIX + str(project) + '/' + queue


class DataDriver(storage.DataDriverBase):
    """Pooling meta-driver for routing requests to multiple backends.

    :param conf: Configuration from which to read pooling options
    :param cache: Cache instance that will be passed to individual
        storage driver instances that correspond to each pool. will
        also be used by the pool controller to reduce latency for
        some operations.
    """

    def __init__(self, conf, cache, control):
        super(DataDriver, self).__init__(conf, cache)
        self._pool_catalog = Catalog(conf, cache, control)

    def is_alive(self):
        cursor = self._pool_catalog._pools_ctrl.list(limit=0)
        pools = next(cursor)
        return all(self._pool_catalog.get_driver(pool['name']).is_alive()
                   for pool in pools)

    def _health(self):
        KPI = {}
        # Leverage the is_alive to indicate if the backend storage is
        # reachable or not
        KPI['catalog_reachable'] = self.is_alive()

        cursor = self._pool_catalog._pools_ctrl.list(limit=0)
        # Messages of each pool
        for pool in next(cursor):
            driver = self._pool_catalog.get_driver(pool['name'])
            KPI[pool['name']] = driver._health()

        return KPI

    def gc(self):
        cursor = self._pool_catalog._pools_ctrl.list(limit=0)
        for pool in next(cursor):
            driver = self._pool_catalog.get_driver(pool['name'])
            driver.gc()

    @decorators.lazy_property(write=False)
    def queue_controller(self):
        return QueueController(self._pool_catalog)

    @decorators.lazy_property(write=False)
    def message_controller(self):
        return MessageController(self._pool_catalog)

    @decorators.lazy_property(write=False)
    def claim_controller(self):
        return ClaimController(self._pool_catalog)


class RoutingController(storage.base.ControllerBase):
    """Routes operations to the appropriate pool.

    This controller stands in for a regular storage controller,
    routing operations to a driver instance that represents
    the pool to which the queue has been assigned.

    Do not instantiate this class directly; use one of the
    more specific child classes instead.
    """

    _resource_name = None

    def __init__(self, pool_catalog):
        super(RoutingController, self).__init__(None)
        self._ctrl_property_name = self._resource_name + '_controller'
        self._pool_catalog = pool_catalog

    @decorators.memoized_getattr
    def __getattr__(self, name):
        # NOTE(kgriffs): Use a closure trick to avoid
        # some attr lookups each time forward() is called.
        lookup = self._pool_catalog.lookup

        # NOTE(kgriffs): Assume that every controller method
        # that is exposed to the transport declares queue name
        # as its first arg. The only exception to this
        # is QueueController.list
        def forward(queue, *args, **kwargs):
            # NOTE(kgriffs): Using .get since 'project' is an
            # optional argument.
            storage = lookup(queue, kwargs.get('project'))
            target_ctrl = getattr(storage, self._ctrl_property_name)
            return getattr(target_ctrl, name)(queue, *args, **kwargs)

        return forward


class QueueController(RoutingController):
    """Controller to facilitate special processing for queue operations."""

    _resource_name = 'queue'

    def __init__(self, pool_catalog):
        super(QueueController, self).__init__(pool_catalog)
        self._lookup = self._pool_catalog.lookup

    def list(self, project=None, marker=None,
             limit=storage.DEFAULT_QUEUES_PER_PAGE, detailed=False):

        def all_pages():
            cursor = self._pool_catalog._pools_ctrl.list(limit=0)
            for pool in next(cursor):
                yield next(self._pool_catalog.get_driver(pool['name'])
                           .queue_controller.list(
                               project=project,
                               marker=marker,
                               limit=limit,
                               detailed=detailed))

        # make a heap compared with 'name'
        ls = heapq.merge(*[
            storage_utils.keyify('name', page)
            for page in all_pages()
        ])

        marker_name = {}

        # limit the iterator and strip out the comparison wrapper
        def it():
            for queue_cmp in itertools.islice(ls, limit):
                marker_name['next'] = queue_cmp.obj['name']
                yield queue_cmp.obj

        yield it()
        yield marker_name['next']

    def get(self, name, project=None):
        try:
            return self.get_metadata(name, project)
        except errors.QueueDoesNotExist:
            return {}

    def create(self, name, metadata=None, project=None):
        flavor = metadata and metadata.get('_flavor', None)
        self._pool_catalog.register(name, project=project, flavor=flavor)

        # NOTE(cpp-cabrera): This should always succeed since we just
        # registered the project/queue. There is a race condition,
        # however. If between the time we register a queue and go to
        # look it up, the queue is deleted, then this assertion will
        # fail.
        target = self._lookup(name, project)
        if not target:
            raise RuntimeError('Failed to register queue')

        return target.queue_controller.create(name,
                                              metadata=metadata,
                                              project=project)

    def delete(self, name, project=None):
        # NOTE(cpp-cabrera): If we fail to find a project/queue in the
        # catalogue for a delete, just ignore it.
        target = self._lookup(name, project)
        if target:

            # NOTE(cpp-cabrera): delete from the catalogue first. If
            # zaqar crashes in the middle of these two operations,
            # it is desirable that the entry be missing from the
            # catalogue and present in storage, rather than the
            # reverse. The former case leads to all operations
            # behaving as expected: 404s across the board, and a
            # functionally equivalent 204 on a create queue. The
            # latter case is more difficult to reason about, and may
            # yield 500s in some operations.
            control = target.queue_controller
            self._pool_catalog.deregister(name, project)
            ret = control.delete(name, project)
            return ret

        return None

    def exists(self, name, project=None, **kwargs):
        target = self._lookup(name, project)
        if target:
            control = target.queue_controller
            return control.exists(name, project=project)
        return False

    def get_metadata(self, name, project=None):
        target = self._lookup(name, project)
        if target:
            control = target.queue_controller
            return control.get_metadata(name, project=project)
        raise errors.QueueDoesNotExist(name, project)

    def set_metadata(self, name, metadata, project=None):
        target = self._lookup(name, project)
        if target:
            control = target.queue_controller
            return control.set_metadata(name, metadata=metadata,
                                        project=project)
        raise errors.QueueDoesNotExist(name, project)

    def stats(self, name, project=None):
        target = self._lookup(name, project)
        if target:
            control = target.queue_controller
            return control.stats(name, project=project)
        raise errors.QueueDoesNotExist(name, project)


class MessageController(RoutingController):
    _resource_name = 'message'

    def __init__(self, pool_catalog):
        super(MessageController, self).__init__(pool_catalog)
        self._lookup = self._pool_catalog.lookup

    def post(self, queue, messages, client_uuid, project=None):
        target = self._lookup(queue, project)
        if target:
            control = target.message_controller
            return control.post(queue, project=project,
                                messages=messages,
                                client_uuid=client_uuid)
        raise errors.QueueDoesNotExist(queue, project)

    def delete(self, queue, message_id, project=None, claim=None):
        target = self._lookup(queue, project)
        if target:
            control = target.message_controller
            return control.delete(queue, project=project,
                                  message_id=message_id, claim=claim)
        return None

    def bulk_delete(self, queue, message_ids=None, project=None):
        target = self._lookup(queue, project)
        if target:
            control = target.message_controller
            return control.bulk_delete(queue, project=project,
                                       message_ids=message_ids)
        return None

    def pop(self, queue, limit, project=None):
        target = self._lookup(queue, project)
        if target:
            control = target.message_controller
            return control.pop(queue, project=project, limit=limit)
        return None

    def bulk_get(self, queue, message_ids, project=None):
        target = self._lookup(queue, project)
        if target:
            control = target.message_controller
            return control.bulk_get(queue, project=project,
                                    message_ids=message_ids)
        return []

    def list(self, queue, project=None, marker=None,
             limit=storage.DEFAULT_MESSAGES_PER_PAGE,
             echo=False, client_uuid=None, include_claimed=False):
        target = self._lookup(queue, project)
        if target:
            control = target.message_controller
            return control.list(queue, project=project,
                                marker=marker, limit=limit,
                                echo=echo, client_uuid=client_uuid,
                                include_claimed=include_claimed)
        return iter([[]])

    def get(self, queue, message_id, project=None):
        target = self._lookup(queue, project)
        if target:
            control = target.message_controller
            return control.get(queue, message_id=message_id,
                               project=project)
        raise errors.QueueDoesNotExist(queue, project)

    def first(self, queue, project=None, sort=1):
        target = self._lookup(queue, project)
        if target:
            control = target.message_controller
            return control.first(queue, project=project, sort=sort)
        raise errors.QueueDoesNotExist(queue, project)


class ClaimController(RoutingController):
    _resource_name = 'claim'

    def __init__(self, pool_catalog):
        super(ClaimController, self).__init__(pool_catalog)
        self._lookup = self._pool_catalog.lookup

    def create(self, queue, metadata, project=None,
               limit=storage.DEFAULT_MESSAGES_PER_CLAIM):
        target = self._lookup(queue, project)
        if target:
            control = target.claim_controller
            return control.create(queue, metadata=metadata,
                                  project=project, limit=limit)
        return [None, []]

    def get(self, queue, claim_id, project=None):
        target = self._lookup(queue, project)
        if target:
            control = target.claim_controller
            return control.get(queue, claim_id=claim_id,
                               project=project)
        raise errors.ClaimDoesNotExist(claim_id, queue, project)

    def update(self, queue, claim_id, metadata, project=None):
        target = self._lookup(queue, project)
        if target:
            control = target.claim_controller
            return control.update(queue, claim_id=claim_id,
                                  project=project, metadata=metadata)
        raise errors.ClaimDoesNotExist(claim_id, queue, project)

    def delete(self, queue, claim_id, project=None):
        target = self._lookup(queue, project)
        if target:
            control = target.claim_controller
            return control.delete(queue, claim_id=claim_id,
                                  project=project)
        return None


class Catalog(object):
    """Represents the mapping between queues and pool drivers."""

    def __init__(self, conf, cache, control):
        self._drivers = {}
        self._conf = conf
        self._cache = cache

        self._conf.register_opts(_CATALOG_OPTIONS, group=_CATALOG_GROUP)
        self._catalog_conf = self._conf[_CATALOG_GROUP]

        self._pools_ctrl = control.pools_controller
        self._flavor_ctrl = control.flavors_controller
        self._catalogue_ctrl = control.catalogue_controller

    # FIXME(cpp-cabrera): https://bugs.launchpad.net/zaqar/+bug/1252791
    def _init_driver(self, pool_id):
        """Given a pool name, returns a storage driver.

        :param pool_id: The name of a pool.
        :type pool_id: six.text_type
        :returns: a storage driver
        :rtype: zaqar.queues.storage.base.DataDriverBase
        """
        pool = self._pools_ctrl.get(pool_id, detailed=True)
        conf = storage_utils.dynamic_conf(pool['uri'], pool['options'],
                                  conf=self._conf)
        return storage_utils.load_storage_driver(conf, self._cache)

    @decorators.caches(_pool_cache_key, _POOL_CACHE_TTL)
    def _pool_id(self, queue, project=None):
        """Get the ID for the pool assigned to the given queue.

        :param queue: name of the queue
        :param project: project to which the queue belongs

        :returns: pool id

        :raises: `errors.QueueNotMapped`
        """
        return self._catalogue_ctrl.get(project, queue)['pool']

    def register(self, queue, project=None, flavor=None):
        """Register a new queue in the pool catalog.

        This method should be called whenever a new queue is being
        created, and will create an entry in the pool catalog for
        the given queue.

        After using this method to register the queue in the
        catalog, the caller should call `lookup()` to get a reference
        to a storage driver which will allow interacting with the
        queue's assigned backend pool.

        :param queue: Name of the new queue to assign to a pool
        :type queue: six.text_type
        :param project: Project to which the queue belongs, or
            None for the "global" or "generic" project.
        :type project: six.text_type
        :param flavor: Flavor for the queue (OPTIONAL)
        :type flavor: six.text_type

        :raises: NoPoolFound

        """

        # NOTE(cpp-cabrera): only register a queue if the entry
        # doesn't exist
        if not self._catalogue_ctrl.exists(project, queue):

            if flavor is not None:
                flavor = self._flavor_ctrl.get(flavor, project=project)
                pools = self._pools_ctrl.get_group(group=flavor['pool'],
                                                   detailed=True)
                pool = select.weighted(pools)
                pool = pool and pool['name'] or None
            else:
                # NOTE(flaper87): Get pools assigned to the default
                # group `None`. We should consider adding a `default_group`
                # option in the future.
                pools = self._pools_ctrl.get_group(detailed=True)
                pool = select.weighted(pools)
                pool = pool and pool['name'] or None

                if not pool:
                    raise errors.NoPoolFound()

            self._catalogue_ctrl.insert(project, queue, pool)

    @_pool_id.purges
    def deregister(self, queue, project=None):
        """Removes a queue from the pool catalog.

        Call this method after successfully deleting it from a
        backend pool.

        :param queue: Name of the new queue to assign to a pool
        :type queue: six.text_type
        :param project: Project to which the queue belongs, or
            None for the "global" or "generic" project.
        :type project: six.text_type
        """
        self._catalogue_ctrl.delete(project, queue)

    def lookup(self, queue, project=None):
        """Lookup a pool driver for the given queue and project.

        :param queue: Name of the queue for which to find a pool
        :param project: Project to which the queue belongs, or
            None to specify the "global" or "generic" project.

        :returns: A storage driver instance for the appropriate pool. If
            the driver does not exist yet, it is created and cached. If the
            queue is not mapped, returns None.
        :rtype: Maybe DataDriver
        """

        try:
            pool_id = self._pool_id(queue, project)
        except errors.QueueNotMapped as ex:
            LOG.debug(ex)

            # NOTE(kgriffs): Return `None`, rather than letting the
            # exception bubble up, so that the higher layer doesn't
            # have to duplicate the try..except..log code all over
            # the place.
            return None

        return self.get_driver(pool_id)

    def get_driver(self, pool_id):
        """Get storage driver, preferably cached, from a pool name.

        :param pool_id: The name of a pool.
        :type pool_id: six.text_type
        :returns: a storage driver
        :rtype: zaqar.queues.storage.base.DataDriver
        """

        try:
            return self._drivers[pool_id]
        except KeyError:
            # NOTE(cpp-cabrera): cache storage driver connection
            self._drivers[pool_id] = self._init_driver(pool_id)

            return self._drivers[pool_id]
