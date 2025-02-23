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

from oslo_log import log as logging
from stevedore import extension

import os_vif.exception
import os_vif.i18n
import os_vif.objects

_LE = os_vif.i18n._LE
_LI = os_vif.i18n._LI

_EXT_MANAGER = None
LOG = logging.getLogger('os_vif')


def initialize(reset=False):
    """
    Loads all os_vif plugins and initializes them with a dictionary of
    configuration options. These configuration options are passed as-is
    to the individual VIF plugins that are loaded via stevedore.

    :param reset: Recreate and load the VIF plugin extensions.

    """
    global _EXT_MANAGER
    if reset or (_EXT_MANAGER is None):
        _EXT_MANAGER = extension.ExtensionManager(namespace='os_vif',
                                                  invoke_on_load=False)
        for plugin_name in _EXT_MANAGER.names():
            cls = _EXT_MANAGER[plugin_name].plugin
            obj = cls.load(plugin_name)
            _EXT_MANAGER[plugin_name].obj = obj

        os_vif.objects.register_all()


def plug(vif, instance_info):
    """
    Given a model of a VIF, perform operations to plug the VIF properly.

    :param vif: `os_vif.objects.VIF` object.
    :param instance_info: `os_vif.objects.InstanceInfo` object.
    :raises `exception.LibraryNotInitialized` if the user of the library
            did not call os_vif.initialize(**config) before trying to
            plug a VIF.
    :raises `exception.NoMatchingPlugin` if there is no plugin for the
            type of VIF supplied.
    :raises `exception.PlugException` if anything fails during unplug
            operations.
    """
    if _EXT_MANAGER is None:
        raise os_vif.exception.LibraryNotInitialized()

    plugin_name = vif.plugin
    try:
        plugin = _EXT_MANAGER[plugin_name].obj
    except KeyError:
        raise os_vif.exception.NoMatchingPlugin(plugin_name=plugin_name)

    try:
        LOG.debug("Plugging vif %s", vif)
        plugin.plug(vif, instance_info)
        LOG.info(_LI("Successfully plugged vif %s"), vif)
    except Exception as err:
        LOG.error(_LE("Failed to plug vif %(vif)s. Got error: %(err)s"),
                  vif=vif, err=err)
        raise os_vif.exception.PlugException(vif=vif, err=err)


def unplug(vif, instance_info):
    """
    Given a model of a VIF, perform operations to unplug the VIF properly.

    :param vif: `os_vif.objects.VIF` object.
    :param instance_info: `os_vif.objects.InstanceInfo` object.
    :raises `exception.LibraryNotInitialized` if the user of the library
            did not call os_vif.initialize(**config) before trying to
            plug a VIF.
    :raises `exception.NoMatchingPlugin` if there is no plugin for the
            type of VIF supplied.
    :raises `exception.UnplugException` if anything fails during unplug
            operations.
    """
    if _EXT_MANAGER is None:
        raise os_vif.exception.LibraryNotInitialized()

    plugin_name = vif.plugin
    try:
        plugin = _EXT_MANAGER[plugin_name].obj
    except KeyError:
        raise os_vif.exception.NoMatchingPlugin(plugin_name=plugin_name)

    try:
        LOG.debug("Unplugging vif %s", vif)
        plugin.unplug(vif, instance_info)
        LOG.info(_LI("Successfully unplugged vif %s"), vif)
    except Exception as err:
        LOG.error(_LE("Failed to unplug vif %(vif)s. Got error: %(err)s"),
                  vif=vif, err=err)
        raise os_vif.exception.UnplugException(vif=vif, err=err)


def host_info():
    """
    Get information about the host platform configuration to be
    provided to the network manager. This will include information
    about what plugins are installed in the host

    :returns: a os_vif.host_info.HostInfo class instance
    """

    if _EXT_MANAGER is None:
        raise os_vif.exception.LibraryNotInitialized()

    plugins = [
        _EXT_MANAGER[name].obj.describe()
        for name in _EXT_MANAGER.names()
    ]

    return os_vif.objects.host_info.HostInfo(plugin_info=plugins)
