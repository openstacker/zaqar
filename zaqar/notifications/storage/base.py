# Copyright (c) 2013 Red Hat, Inc.
# Copyright 2014 Catalyst IT Ltd
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

"""Implements the DriverBase abstract class for Zaqar storage drivers."""

import abc
import functools
import time
import uuid

from oslo.config import cfg
import six

import zaqar.openstack.common.log as logging

DEFAULT_SUBSCRIPTIONS_PER_PAGE = 10

LOG = logging.getLogger(__name__)


@six.add_metaclass(abc.ABCMeta)
class DriverBase(object):
    """Base class for both data and control plane drivers

    :param conf: Configuration containing options for this driver.
    :type conf: `oslo.config.ConfigOpts`
    :param cache: Cache instance to use for reducing latency
        for certain lookups.
    :type cache: `zaqar.openstack.common.cache.backends.BaseCache`
    """
    _DRIVER_OPTIONS = []

    def __init__(self, conf, cache):
        self.conf = conf
        self.cache = cache
        self._register_opts()

    def _register_opts(self):
        for group, options in self._DRIVER_OPTIONS:
            for opt in options:
                try:
                    self.conf.register_opt(opt, group=group)
                except cfg.DuplicateOptError:
                    pass


@six.add_metaclass(abc.ABCMeta)
class DataDriverBase(DriverBase):
    """Interface definition for storage drivers.

    Data plane storage drivers are responsible for implementing the
    core functionality of the system.

    Connection information and driver-specific options are
    loaded from the config file or the pool catalog.

    :param conf: Configuration containing options for this driver.
    :type conf: `oslo.config.ConfigOpts`
    :param cache: Cache instance to use for reducing latency
        for certain lookups.
    :type cache: `zaqar.openstack.common.cache.backends.BaseCache`
    """

    def __init__(self, conf, cache):
        super(DataDriverBase, self).__init__(conf, cache)

    @abc.abstractmethod
    def is_alive(self):
        """Check whether the storage is ready."""
        raise NotImplementedError

    def health(self):
        """Return the health status of service."""
        overall_health = {}
        # NOTE(flwang): KPI extracted from different storage backends,
        # _health() will be implemented by different storage drivers.
        backend_health = self._health()
        if backend_health:
            overall_health.update(backend_health)

        return overall_health

    @abc.abstractmethod
    def _health(self):
        """Return the health status based on different backends."""
        raise NotImplementedError

    def _get_operation_status(self):
        op_status = {}
        status_template = lambda s, t, r: {'succeeded': s,
                                           'seconds': t,
                                           'ref': r}
        project = str(uuid.uuid4())
        queue = str(uuid.uuid4())
        client = str(uuid.uuid4())
        msg_template = lambda s: {'ttl': 600, 'body': {'event': 'p_%s' % s}}
        messages = [msg_template(i) for i in range(100)]
        claim_metadata = {'ttl': 60, 'grace': 300}

        # NOTE (flwang): Using time.time() instead of timeit since timeit will
        # make the method calling be complicated.
        def _handle_status(operation_type, callable_operation):
            succeeded = True
            ref = None
            result = None
            try:
                start = time.time()
                result = callable_operation()
            except Exception as e:
                ref = str(uuid.uuid4())
                LOG.exception(e, extra={'instance_uuid': ref})
                succeeded = False
            status = status_template(succeeded, time.time() - start, ref)
            op_status[operation_type] = status
            return succeeded, result

        # create queue
        func = functools.partial(self.queue_controller.create,
                                 queue, project=project)
        succeeded, _ = _handle_status('create_queue', func)

        # post messages
        if succeeded:
            func = functools.partial(self.message_controller.post,
                                     queue, messages, client, project=project)
            _, msg_ids = _handle_status('post_messages', func)

            # claim messages
            if msg_ids:
                func = functools.partial(self.claim_controller.create,
                                         queue, claim_metadata,
                                         project=project)
                _, (claim_id, claim_msgs) = _handle_status('claim_messages',
                                                           func)

                # list messages
                func = functools.partial(self.message_controller.list,
                                         queue, project, echo=True,
                                         client_uuid=client,
                                         include_claimed=True)
                _handle_status('list_messages', func)

                # delete messages
                if claim_id and claim_msgs:
                    for message in claim_msgs:
                        func = functools.partial(self.
                                                 message_controller.delete,
                                                 queue, message['id'],
                                                 project, claim=claim_id)
                        succeeded, _ = _handle_status('delete_messages', func)
                        if not succeeded:
                            break
                    # delete claim
                    func = functools.partial(self.claim_controller.delete,
                                             queue, claim_id, project)
                    _handle_status('delete_claim', func)

            # delete queue
            func = functools.partial(self.queue_controller.delete,
                                     queue, project=project)
            _handle_status('delete_queue', func)
        return op_status

    def gc(self):
        """Perform manual garbage collection of claims and messages.

        This method can be overridden in order to provide a trigger
        that can be called by so-called "garbage collection" scripts
        that are required by some drivers.

        By default, this method does nothing.
        """
        pass

    @abc.abstractproperty
    def subscription_controller(self):
        """Returns the driver's queue controller."""
        raise NotImplementedError


class ControllerBase(object):
    """Top-level class for controllers.

    :param driver: Instance of the driver
        instantiating this controller.
    """

    def __init__(self, driver):
        self.driver = driver


@six.add_metaclass(abc.ABCMeta)
class Subscription(ControllerBase):
    """This class is responsible for managing queues.

    Queue operations include CRUD, monitoring, etc.

    Storage driver implementations of this class should
    be capable of handling high workloads and huge
    numbers of queues.
    """

    @abc.abstractmethod
    def list(self, project=None, marker=None,
             limit=DEFAULT_SUBSCRIPTIONS_PER_PAGE, detailed=False):
        """Base method for listing queues.

        :param project: Project id
        :param marker: The last queue name
        :param limit: (Default 10) Max number of queues to return
        :param detailed: Whether metadata is included

        :returns: An iterator giving a sequence of queues
            and the marker of the next page.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def get(self, name, project=None):
        """Base method for queue metadata retrieval.

        :param name: The queue name
        :param project: Project id

        :returns: Dictionary containing queue metadata
        :raises: DoesNotExist
        """
        raise NotImplementedError

    def get_metadata(self, name, project=None):
        """Base method for queue metadata retrieval.

        :param name: The queue name
        :param project: Project id

        :returns: Dictionary containing queue metadata
        :raises: DoesNotExist
        """
        raise NotImplementedError

    @abc.abstractmethod
    def create(self, name, metadata=None, project=None):
        """Base method for queue creation.

        :param name: The queue name
        :param project: Project id
        :returns: True if a queue was created and False
            if it was updated.
        """
        raise NotImplementedError

    @abc.abstractmethod
    def exists(self, name, project=None):
        """Base method for testing queue existence.

        :param name: The queue name
        :param project: Project id
        :returns: True if a queue exists and False
            if it does not.
        """
        raise NotImplementedError

    def set_metadata(self, name, metadata, project=None):
        """Base method for updating a queue metadata.

        :param name: The queue name
        :param metadata: Queue metadata as a dict
        :param project: Project id
        :raises: DoesNotExist
        """
        raise NotImplementedError

    @abc.abstractmethod
    def delete(self, name, project=None):
        """Base method for deleting a queue.

        :param name: The queue name
        :param project: Project id
        """
        raise NotImplementedError

    @abc.abstractmethod
    def stats(self, name, project=None):
        """Base method for queue stats.

        :param name: The queue name
        :param project: Project id
        :returns: Dictionary with the
            queue stats
        """
        raise NotImplementedError
