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

"""Implements vlans, bridges, and iptables rules using linux utilities."""

import os

from oslo_concurrency import lockutils
from oslo_concurrency import processutils
from oslo_log import log as logging

LOG = logging.getLogger(__name__)
_IPTABLES_MANAGER = None


def device_exists(device):
    """Check if ethernet device exists."""
    return os.path.exists('/sys/class/net/%s' % device)


def _set_device_mtu(dev, mtu):
    """Set the device MTU."""
    processutils.execute('ip', 'link', 'set', dev, 'mtu', mtu,
                         check_exit_code=[0, 2, 254])


def _ip_bridge_cmd(action, params, device):
    """Build commands to add/del ips to bridges/devices."""
    cmd = ['ip', 'addr', action]
    cmd.extend(params)
    cmd.extend(['dev', device])
    return cmd


def ensure_vlan_bridge(vlan_num, bridge, bridge_interface,
                       net_attrs=None, mac_address=None,
                       mtu=None):
    """Create a vlan and bridge unless they already exist."""
    interface = ensure_vlan(vlan_num, bridge_interface, mac_address, mtu=mtu)
    ensure_bridge(bridge, interface, net_attrs)
    return interface


@lockutils.synchronized('nova-lock_vlan', external=True)
def ensure_vlan(vlan_num, bridge_interface, mac_address=None, mtu=None):
    """Create a vlan unless it already exists."""
    interface = 'vlan%s' % vlan_num
    if not device_exists(interface):
        LOG.debug('Starting VLAN interface %s', interface)
        processutils.execute('ip', 'link', 'add', 'link',
                             bridge_interface, 'name', interface, 'type',
                             'vlan', 'id', vlan_num,
                             check_exit_code=[0, 2, 254],
                             run_as_root=True)
        # (danwent) the bridge will inherit this address, so we want to
        # make sure it is the value set from the NetworkManager
        if mac_address:
            processutils.execute('ip', 'link', 'set', interface,
                                 'address', mac_address,
                                 check_exit_code=[0, 2, 254],
                                 run_as_root=True)
        processutils.execute('ip', 'link', 'set', interface, 'up',
                             check_exit_code=[0, 2, 254],
                             run_as_root=True)
    # NOTE(vish): set mtu every time to ensure that changes to mtu get
    #             propogated
    _set_device_mtu(interface, mtu)
    return interface


@lockutils.synchronized('nova-lock_bridge', external=True)
def ensure_bridge(bridge, interface, net_attrs=None, gateway=True,
                  filtering=True):
    """Create a bridge unless it already exists.

    :param interface: the interface to create the bridge on.
    :param net_attrs: dictionary with  attributes used to create bridge.
    :param gateway: whether or not the bridge is a gateway.
    :param filtering: whether or not to create filters on the bridge.

    If net_attrs is set, it will add the net_attrs['gateway'] to the bridge
    using net_attrs['broadcast'] and net_attrs['cidr'].  It will also add
    the ip_v6 address specified in net_attrs['cidr_v6'] if use_ipv6 is set.

    The code will attempt to move any ips that already exist on the
    interface onto the bridge and reset the default gateway if necessary.

    """
    if not device_exists(bridge):
        LOG.debug('Starting Bridge %s', bridge)
        processutils.execute('brctl', 'addbr', bridge,
                             run_as_root=True)
        processutils.execute('brctl', 'setfd', bridge, 0,
                             run_as_root=True)
        # processutils.execute('brctl setageing %s 10' % bridge,
        #                      run_as_root=True)
        processutils.execute('brctl', 'stp', bridge, 'off',
                             run_as_root=True)
        # (danwent) bridge device MAC address can't be set directly.
        # instead it inherits the MAC address of the first device on the
        # bridge, which will either be the vlan interface, or a
        # physical NIC.
        processutils.execute('ip', 'link', 'set', bridge, 'up',
                             run_as_root=True)

    if interface:
        LOG.debug('Adding interface %(interface)s to bridge %(bridge)s',
                  {'interface': interface, 'bridge': bridge})
        out, err = processutils.execute('brctl', 'addif', bridge,
                                        interface, check_exit_code=False,
                                        run_as_root=True)
        if (err and err != "device %s is already a member of a bridge; "
              "can't enslave it to bridge %s.\n" % (interface, bridge)):
            msg = _('Failed to add interface: %s') % err
            raise Exception(msg)

        out, err = processutils.execute('ip', 'link', 'set',
                                        interface, 'up', check_exit_code=False,
                                        run_as_root=True)

        # NOTE(vish): This will break if there is already an ip on the
        #             interface, so we move any ips to the bridge
        # NOTE(danms): We also need to copy routes to the bridge so as
        #              not to break existing connectivity on the interface
        old_routes = []
        out, err = processutils.execute('ip', 'route', 'show', 'dev',
                                        interface)
        for line in out.split('\n'):
            fields = line.split()
            if fields and 'via' in fields:
                old_routes.append(fields)
                processutils.execute('ip', 'route', 'del', *fields,
                                     run_as_root=True)
        out, err = processutils.execute('ip', 'addr', 'show', 'dev', interface,
                                        'scope', 'global')
        for line in out.split('\n'):
            fields = line.split()
            if fields and fields[0] == 'inet':
                if fields[-2] in ('secondary', 'dynamic', ):
                    params = fields[1:-2]
                else:
                    params = fields[1:-1]
                processutils.execute(*_ip_bridge_cmd('del', params,
                                                     fields[-1]),
                                     check_exit_code=[0, 2, 254],
                                     run_as_root=True)
                processutils.execute(*_ip_bridge_cmd('add', params,
                                                     bridge),
                                     check_exit_code=[0, 2, 254],
                                     run_as_root=True)
        for fields in old_routes:
            processutils.execute('ip', 'route', 'add', *fields,
                                 run_as_root=True)

    if filtering:
        # Don't forward traffic unless we were told to be a gateway
        global _IPTABLES_MANAGER
        ipv4_filter = _IPTABLES_MANAGER.ipv4['filter']
        if gateway:
            for rule in _IPTABLES_MANAGER.get_gateway_rules(bridge):
                ipv4_filter.add_rule(*rule)
        else:
            ipv4_filter.add_rule('FORWARD',
                                 ('--in-interface %s -j %s'
                                  % (bridge,
                                     _IPTABLES_MANAGER.iptables_drop_action)))
            ipv4_filter.add_rule('FORWARD',
                                 ('--out-interface %s -j %s'
                                  % (bridge,
                                     _IPTABLES_MANAGER.iptables_drop_action)))
        _IPTABLES_MANAGER.apply()


def configure(iptables_mgr):
    """Configure the iptables manager impl.

    :param iptables_mgr: the iptables manager instance
    """
    global _IPTABLES_MANAGER
    _IPTABLES_MANAGER = iptables_mgr
