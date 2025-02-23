# Derived from nova/network/linux_net.py
#
# Copyright (c) 2011 X.commerce, a business unit of eBay Inc.
# Copyright 2010 United States Government as represented by the
# Administrator of the National Aeronautics and Space Administration.
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""Implements vlans, bridges using linux utilities."""

import os

from oslo_concurrency import processutils
from oslo_log import log as logging
from oslo_utils import excutils

from vif_plug_ovs import exception
from vif_plug_ovs.i18n import _LE

LOG = logging.getLogger(__name__)


def _ovs_vsctl(args, timeout=None):
    full_args = ['ovs-vsctl']
    if timeout is not None:
        full_args += ['--timeout=%s' % timeout]
    full_args += args
    try:
        return processutils.execute(*full_args, run_as_root=True)
    except Exception as e:
        LOG.error(_LE("Unable to execute %(cmd)s. Exception: %(exception)s"),
                  {'cmd': full_args, 'exception': e})
        raise exception.AgentError(method=full_args)


def create_ovs_vif_port(bridge, dev, iface_id, mac, instance_id, mtu,
                        timeout=None):
    _ovs_vsctl(['--', '--if-exists', 'del-port', dev, '--',
                'add-port', bridge, dev,
                '--', 'set', 'Interface', dev,
                'external-ids:iface-id=%s' % iface_id,
                'external-ids:iface-status=active',
                'external-ids:attached-mac=%s' % mac,
                'external-ids:vm-uuid=%s' % instance_id],
                timeout=timeout)
    _set_device_mtu(dev, mtu)


def delete_ovs_vif_port(bridge, dev, timeout=None):
    _ovs_vsctl(['--', '--if-exists', 'del-port', bridge, dev],
               timeout=timeout)
    delete_net_dev(dev)


def device_exists(device):
    """Check if ethernet device exists."""
    return os.path.exists('/sys/class/net/%s' % device)


def delete_net_dev(dev):
    """Delete a network device only if it exists."""
    if device_exists(dev):
        try:
            processutils.execute('ip', 'link', 'delete', dev,
                                 check_exit_code=[0, 2, 254],
                                 run_as_root=True)
            LOG.debug("Net device removed: '%s'", dev)
        except processutils.ProcessExecutionError:
            with excutils.save_and_reraise_exception():
                LOG.error(_LE("Failed removing net device: '%s'"), dev)


def create_veth_pair(dev1_name, dev2_name, mtu):
    """Create a pair of veth devices with the specified names,
    deleting any previous devices with those names.
    """
    for dev in [dev1_name, dev2_name]:
        delete_net_dev(dev)

    processutils.execute('ip', 'link', 'add', dev1_name,
                         'type', 'veth', 'peer', 'name', dev2_name,
                         run_as_root=True)
    for dev in [dev1_name, dev2_name]:
        processutils.execute('ip', 'link', 'set', dev, 'up',
                             run_as_root=True)
        processutils.execute('ip', 'link', 'set', dev, 'promisc', 'on')
        _set_device_mtu(dev, mtu)


def _set_device_mtu(dev, mtu):
    """Set the device MTU."""
    processutils.execute('ip', 'link', 'set', dev, 'mtu', mtu,
                         check_exit_code=[0, 2, 254])
