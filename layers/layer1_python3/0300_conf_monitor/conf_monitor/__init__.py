#!/usr/bin/env python3

import os
import signal
import time
import hashlib
from pathlib import Path
from inotify_simple import flags, INotify
from mflog import getLogger
from mfutil import BashWrapperOrRaise, BashWrapper
from mfutil.plugins import get_installed_plugins

RUN = True
LOGGER = getLogger("conf_monitor")
MODULE_RUNTIME_HOME = os.environ.get('MODULE_RUNTIME_HOME', None)
MODULE_RUNTIME_USER = os.environ.get('MODULE_RUNTIME_USER', None)
MODULE_HOME = os.environ['MODULE_HOME']


def handler_stop_signals(signum, frame):
    global RUN
    LOGGER.info("stop signal caught => preparing shutdown...")
    RUN = False


def init_signals():
    signal.signal(signal.SIGINT, handler_stop_signals)
    signal.signal(signal.SIGTERM, handler_stop_signals)


def is_status_running():
    try:
        with open("%s/var/status" % MODULE_RUNTIME_HOME, 'r') as f:
            status = f.read().strip()
    except Exception:
        status = "unknown"
    return ("RUNNING" in status)


def is_running_plugin_install():
    cmd = "pgrep -u '%s' -f 'plugins.install' |wc -l" % MODULE_RUNTIME_USER
    x = BashWrapper(cmd)
    # >1 and not 0 because with BashWrapper, it counts itself
    if int(x.stdout) > 1:
        return True
    cmd = "pgrep -u '%s' -f 'plugins.uninstall' |wc -l" % MODULE_RUNTIME_USER
    x = BashWrapper(cmd)
    # >1 and not 0 because with BashWrapper, it counts itself
    if int(x.stdout) > 1:
        return True
    cmd = "pgrep -u '%s' -f '_plugins.develop' |wc -l" % MODULE_RUNTIME_USER
    x = BashWrapper(cmd)
    # >1 and not 0 because with BashWrapper, it counts itself
    if int(x.stdout) > 1:
        return True
    return False


def _get_plugins_home():
    plugins = get_installed_plugins()
    return [x['home'] for x in plugins]


def get_plugins_config_ini():
    res = []
    for home in _get_plugins_home():
        config_path = "%s/config.ini" % home
        if os.path.exists(config_path):
            res.append(config_path)
    return res


def get_plugins_crontab():
    res = []
    for home in _get_plugins_home():
        config_path = "%s/crontab" % home
        if os.path.exists(config_path):
            res.append(config_path)
    return res


def md5sumfile(path):
    with open(path, 'r') as f:
        c = f.read()
    return hashlib.md5(c.encode('utf8')).hexdigest()


def make_new_circus_conf():
    new_circus_conf = "%s/tmp/tmp_circus_conf2" % MODULE_RUNTIME_HOME
    cmd = "_make_circus_conf >%s" % new_circus_conf
    BashWrapperOrRaise(cmd)
    return (new_circus_conf, md5sumfile(new_circus_conf))


def make_new_crontab_conf():
    new_crontab_conf = "%s/tmp/tmp_crontab_conf2" % MODULE_RUNTIME_HOME
    cmd = "_make_crontab.sh >%s" % new_crontab_conf
    BashWrapperOrRaise(cmd)
    return (new_crontab_conf, md5sumfile(new_crontab_conf))


def get_old_circus_conf():
    old_circus_conf = "%s/tmp/config_auto/circus.ini" % MODULE_RUNTIME_HOME
    return (old_circus_conf, md5sumfile(old_circus_conf))


def get_old_crontab_conf():
    old_crontab_conf = "%s/tmp/config_auto/crontab" % MODULE_RUNTIME_HOME
    return (old_crontab_conf, md5sumfile(old_crontab_conf))


def restart_circus(old_conf, new_conf):
    os.unlink(old_conf)
    os.rename(new_conf, old_conf)
    os.system("_circus.reload &")


def deploy_crontab(old_conf, new_conf):
    os.unlink(old_conf)
    os.rename(new_conf, old_conf)
    cmd = "_uninstall_crontab.sh"
    x = BashWrapper(cmd)
    if not x:
        LOGGER.warning(x)
    cmd = "deploycron_file '%s'" % old_conf
    x = BashWrapper(cmd)
    if not x:
        LOGGER.warning(x)


def register_watches(ih, wds):
    paths = \
        get_plugins_config_ini() + ["%s/config/nginx.conf" % MODULE_HOME,
                                    "%s/config/circus.ini" % MODULE_HOME] + \
        get_plugins_crontab() + ["%s/var/plugins" % MODULE_RUNTIME_HOME,
                                 "%s/var/conf_monitor" % MODULE_RUNTIME_HOME]
    for path in paths:
        register_watch(ih, wds, path)
    wds_to_unregister = []
    for wd, path in wds.items():
        if path not in paths:
            wds_to_unregister.append(wd)
    for wd in wds_to_unregister:
        unregister_watch(ih, wds, wd)


def unregister_watch(ih, wds, wd):
    LOGGER.info("Unregistering inotify watch on %s" % wds[wd])
    try:
        ih.rm_watch(wd)
    except Exception:
        pass
    try:
        del(wds[wd])
    except Exception:
        pass


def register_watch(ih, wds, path):
    watch_flags = flags.CLOSE_WRITE | flags.CREATE | flags.DELETE |\
        flags.DELETE_SELF | flags.ATTRIB
    if path in wds.values():
        return
    try:
        wd = ih.add_watch(path, watch_flags)
        wds[wd] = path
        LOGGER.info("Registering inotify watch on %s" % path)
    except Exception:
        pass


def touch_control_file():
    Path("%s/var/conf_monitor" % MODULE_RUNTIME_HOME).touch()


class ConfMonitorRunner(object):

    def __init(self, *args, **kwargs):
        pass

    def run(self):
        LOGGER.info("starting")
        init_signals()
        ih = INotify()
        wds = {}
        register_watches(ih, wds)
        touch_control_file()
        got_events = True
        while RUN:
            if got_events:
                LOGGER.info("waiting for events...")
            events = ih.read(1000)
            if events is None or len(events) == 0:
                got_events = False
                continue
            for event in events:
                if not (event.mask & flags.IGNORED):
                    if event.mask & flags.DELETE_SELF:
                        path = wds[event.wd]
                        unregister_watch(ih, wds, event.wd)
                        register_watch(ih, wds, path)
            LOGGER.info("got events")
            got_events = True
            if not is_status_running():
                LOGGER.info("The module is not RUNNING => ignoring...")
                touch_control_file()
                time.sleep(2)
                continue
            if is_running_plugin_install():
                LOGGER.info("plugin installation/uninstallation in progress "
                            "=> ignoring...")
                touch_control_file()
                time.sleep(2)
                continue
            ret = self.handle_event()
            if not ret:
                break
            register_watches(ih, wds)
        LOGGER.info("stopped")

    def manage_circus(self):
        new_conf, new_md5 = make_new_circus_conf()
        old_conf, old_md5 = get_old_circus_conf()
        if new_md5 != old_md5:
            LOGGER.info("circus conf changed => restart circus...")
            restart_circus(old_conf, new_conf)
            time.sleep(3)
            return False
        else:
            LOGGER.debug("circus conf didn't change")
        return True

    def manage_crontab(self):
        new_conf, new_md5 = make_new_crontab_conf()
        old_conf, old_md5 = get_old_crontab_conf()
        if new_md5 != old_md5:
            LOGGER.info("crontab conf changed => changing crontab...")
            deploy_crontab(old_conf, new_conf)
            time.sleep(3)
        else:
            LOGGER.debug("crontab conf didn't change")
        return True

    def handle_event(self):
        return self.manage_circus() and self.manage_crontab()
