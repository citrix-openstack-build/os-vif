# Derived from nova/virt/libvirt/vif.py
#
# Copyright (C) 2011 Midokura KK
# Copyright (C) 2011 Nicira, Inc
# Copyright 2011 OpenStack Foundation
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

import os.path
from os_vif import objects
from os_vif import plugin
from oslo_config import cfg

from oslo_concurrency import processutils

from vif_plug_ovs import exception
from vif_plug_ovs import linux_net


class OvsHybridPlugin(plugin.PluginBase):
    """
    An OVS VIF type that uses a pair of devices in order to allow
    security group rules to be applied to traffic coming in or out of
    a virtual machine.
    """

    NIC_NAME_LEN = 14

    CONFIG_OPTS = (
        cfg.IntOpt('network_device_mtu',
                   default=1500,
                   help='MTU setting for network interface.',
                   deprecated_group="DEFAULT"),
        cfg.IntOpt('ovs_vsctl_timeout',
                   default=120,
                   help='Amount of time, in seconds, that ovs_vsctl should '
                   'wait for a response from the database. 0 is to wait '
                   'forever.',
                   deprecated_group="DEFAULT"),
    )

    @staticmethod
    def get_veth_pair_names(vif):
        iface_id = vif.id
        return (("qvb%s" % iface_id)[:OvsHybridPlugin.NIC_NAME_LEN],
                ("qvo%s" % iface_id)[:OvsHybridPlugin.NIC_NAME_LEN])

    def describe(self):
        return objects.host_info.HostPluginInfo(
            plugin_name="ovs_hybrid",
            vif_info=[
                objects.host_info.HostVIFInfo(
                    vif_object_name=objects.vif.VIFBridge.__name__,
                    min_version="1.0",
                    max_version="1.0")
            ])

    def plug(self, vif, instance_info):
        """Plug using hybrid strategy

        Create a per-VIF linux bridge, then link that bridge to the OVS
        integration bridge via a veth device, setting up the other end
        of the veth device just like a normal OVS port. Then boot the
        VIF on the linux bridge using standard libvirt mechanisms.
        """

        if not hasattr(vif, "port_profile"):
            raise exception.MissingPortProfile()
        if not isinstance(vif.port_profile,
                          objects.vif.VIFPortProfileOpenVSwitch):
            raise exception.WrongPortProfile(
                profile=vif.port_profile.__class__.__name__)

        v1_name, v2_name = self.get_veth_pair_names(vif)

        if not linux_net.device_exists(vif.bridge_name):
            processutils.execute('brctl', 'addbr', vif.bridge_name,
                                 run_as_root=True)
            processutils.execute('brctl', 'setfd', vif.bridge_name, 0,
                                 run_as_root=True)
            processutils.execute('brctl', 'stp', vif.bridge_name, 'off',
                                 run_as_root=True)
            syspath = '/sys/class/net/%s/bridge/multicast_snooping'
            syspath = syspath % vif.bridge_name
            processutils.execute('tee', syspath, process_input='0',
                                 check_exit_code=[0, 1],
                                 run_as_root=True)
            disv6 = ('/proc/sys/net/ipv6/conf/%s/disable_ipv6' %
                     vif.bridge_name)
            if os.path.exists(disv6):
                processutils.execute('tee',
                                     disv6,
                                     process_input='1',
                                     run_as_root=True,
                                     check_exit_code=[0, 1])

        if not linux_net.device_exists(v2_name):
            linux_net.create_veth_pair(v1_name, v2_name,
                                       self.config.network_device_mtu)
            processutils.execute('ip', 'link', 'set', vif.bridge_name, 'up',
                                 run_as_root=True)
            processutils.execute('brctl', 'addif', vif.bridge_name, v1_name,
                                 run_as_root=True)
            linux_net.create_ovs_vif_port(
                vif.network.bridge,
                v2_name,
                vif.port_profile.interface_id,
                vif.address, instance_info.uuid,
                self.config.network_device_mtu,
                timeout=self.config.ovs_vsctl_timeout)

    def unplug(self, vif, instance_info):
        """UnPlug using hybrid strategy

        Unhook port from OVS, unhook port from bridge, delete
        bridge, and delete both veth devices.
        """
        if not hasattr(vif, "port_profile"):
            raise exception.MissingPortProfile()
        if not isinstance(vif.port_profile,
                          objects.vif.VIFPortProfileOpenVSwitch):
            raise exception.WrongPortProfile(
                profile=vif.port_profile.__class__.__name__)

        v1_name, v2_name = self.get_veth_pair_names(vif)

        if linux_net.device_exists(vif.bridge_name):
            processutils.execute('brctl', 'delif', vif.bridge_name, v1_name,
                                 run_as_root=True)
            processutils.execute('ip', 'link', 'set', vif.bridge_name, 'down',
                                 run_as_root=True)
            processutils.execute('brctl', 'delbr', vif.bridge_name,
                                 run_as_root=True)

        linux_net.delete_ovs_vif_port(vif.network.bridge, v2_name,
                                      timeout=self.config.ovs_vsctl_timeout)
