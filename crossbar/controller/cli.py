#####################################################################################
#
#  Copyright (C) Tavendo GmbH
#
#  Unless a separate license agreement exists between you and Tavendo GmbH (e.g. you
#  have purchased a commercial license), the license terms below apply.
#
#  Should you enter into a separate license agreement after having received a copy of
#  this software, then the terms of such license agreement replace the terms below at
#  the time at which such license agreement becomes effective.
#
#  In case a separate license agreement ends, and such agreement ends without being
#  replaced by another separate license agreement, the license terms below apply
#  from the time at which said agreement ends.
#
#  LICENSE TERMS
#
#  This program is free software: you can redistribute it and/or modify it under the
#  terms of the GNU Affero General Public License, version 3, as published by the
#  Free Software Foundation. This program is distributed in the hope that it will be
#  useful, but WITHOUT ANY WARRANTY; without even the implied warranty of
#  MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.
#
#  See the GNU Affero General Public License Version 3 for more details.
#
#  You should have received a copy of the GNU Affero General Public license along
#  with this program. If not, see <http://www.gnu.org/licenses/agpl-3.0.en.html>.
#
#####################################################################################

from __future__ import absolute_import

import os
import sys
import signal
import json
import argparse
import pkg_resources
import platform
import traceback

import click

from twisted.python.reflect import qual

from autobahn.util import utcnow
from autobahn.twisted.choosereactor import install_reactor

import crossbar
from crossbar._logging import make_logger


try:
    import psutil
    _HAS_PSUTIL = True
except ImportError:
    _HAS_PSUTIL = False

_HAS_COLOR_TERM = False
try:
    import colorama

    # https://github.com/tartley/colorama/issues/48
    term = None
    if sys.platform == 'win32' and 'TERM' in os.environ:
        term = os.environ.pop('TERM')

    colorama.init()
    _HAS_COLOR_TERM = True

    if term:
        os.environ['TERM'] = term

except ImportError:
    pass

__all__ = ('run',)

# http://patorjk.com/software/taag/#p=display&h=1&f=Stick%20Letters&t=Crossbar.io
BANNER = r"""     __  __  __  __  __  __      __     __
    /  `|__)/  \/__`/__`|__) /\ |__)  |/  \
    \__,|  \\__/.__/.__/|__)/~~\|  \. |\__/

"""

_PID_FILENAME = 'node.pid'


def check_pid_exists(pid):
    """
    Check if a process with given PID exists.

    :returns: ``True`` if a process exists.
    :rtype: bool
    """
    if sys.platform == 'win32':
        if _HAS_PSUTIL:
            # http://pythonhosted.org/psutil/#psutil.pid_exists
            return psutil.pid_exists(pid)
        else:
            # On Windows, this can only be done with native code (like via win32com, ctypes or psutil).
            # We use psutil.
            raise Exception("cannot check if process with PID exists - package psutil not installed")
    else:
        # Unix-like OS
        # http://stackoverflow.com/a/568285/884770
        try:
            os.kill(pid, 0)
        except OSError:
            return False
        else:
            return True


def _is_crossbar_process(cmdline):
    """
    Returns True if the cmdline passed appears to really be a running
    crossbar instance.
    """
    if len(cmdline) > 1 and 'crossbar' in cmdline[1]:
        return True
    if cmdline[0] == 'crossbar-controller':
        return True
    return False


def check_is_running(cbdir):
    """
    Check if a Crossbar.io node is already running on a Crossbar.io node directory.

    :param cbdir: The Crossbar.io node directory to check.
    :type cbdir: str

    :returns: The PID of the running Crossbar.io controller process or ``None``
    :rtype: int or None
    """
    log = make_logger()

    fp = os.path.join(cbdir, _PID_FILENAME)
    if os.path.isfile(fp):
        with open(fp) as fd:
            pid_data_str = fd.read()
            try:
                pid_data = json.loads(pid_data_str)
                pid = int(pid_data['pid'])
            except ValueError:
                try:
                    os.remove(fp)
                except Exception as e:
                    log.info("Could not remove corrupted Crossbar.io PID file {} - {}".format(fp, e))
                else:
                    log.info("Corrupted Crossbar.io PID file {} removed".format(fp))
            else:
                if sys.platform == 'win32' and not _HAS_PSUTIL:
                    # when on Windows, and we can't actually determine if the PID exists,
                    # just assume it exists
                    return pid_data
                else:
                    pid_exists = check_pid_exists(pid)
                    if pid_exists:
                        if _HAS_PSUTIL:
                            # additionally check this is actually a crossbar process
                            p = psutil.Process(pid)
                            cmdline = p.cmdline()
                            if not _is_crossbar_process(cmdline):
                                nicecmdline = ' '.join(cmdline)
                                if len(nicecmdline) > 76:
                                    nicecmdline = nicecmdline[:38] + ' ... ' + nicecmdline[-38:]
                                log.info('"{}" points to PID {} which is not a crossbar process:'.format(fp, pid))
                                log.info('  ' + nicecmdline)
                                log.info('Verify manually and either kill {} or delete {}'.format(pid, fp))
                                return None
                        return pid_data
                    else:
                        try:
                            os.remove(fp)
                        except Exception as e:
                            log.info("Could not remove stale Crossbar.io PID file {} (pointing to non-existing process with PID {}) - {}".format(fp, pid, e))
                        else:
                            log.info("Stale Crossbar.io PID file {} (pointing to non-existing process with PID {}) removed".format(fp, pid))
    return None


def run_command_version(options, reactor=None, debug=False, **kwargs):
    """
    Subcommand "crossbar version".
    """
    # Python
    #
    py_ver = '.'.join([str(x) for x in list(sys.version_info[:3])])
    if debug:
        py_ver += " [%s]" % sys.version.replace('\n', ' ')

    if 'pypy_version_info' in sys.__dict__:
        py_ver_detail = "{}-{}".format(platform.python_implementation(), '.'.join(str(x) for x in sys.pypy_version_info[:3]))
    else:
        py_ver_detail = platform.python_implementation()

    # Twisted / Reactor
    #
    tx_ver = "%s-%s" % (pkg_resources.require("Twisted")[0].version, reactor.__class__.__name__)
    if debug:
        tx_ver += " [%s]" % qual(reactor.__class__)

    # Autobahn
    #
    from autobahn.websocket.protocol import WebSocketProtocol
    ab_ver = pkg_resources.require("autobahn")[0].version
    if debug:
        ab_ver += " [%s]" % qual(WebSocketProtocol)

    # UTF8 Validator
    #
    from autobahn.websocket.utf8validator import Utf8Validator
    s = qual(Utf8Validator)
    if 'wsaccel' in s:
        utf8_ver = 'wsaccel-%s' % pkg_resources.require('wsaccel')[0].version
    elif s.startswith('autobahn'):
        utf8_ver = 'autobahn'
    else:
        # could not detect UTF8 validator type/version
        utf8_ver = '?'
    if debug:
        utf8_ver += " [%s]" % qual(Utf8Validator)

    # XOR Masker
    #
    from autobahn.websocket.xormasker import XorMaskerNull
    s = qual(XorMaskerNull)
    if 'wsaccel' in s:
        xor_ver = 'wsaccel-%s' % pkg_resources.require('wsaccel')[0].version
    elif s.startswith('autobahn'):
        xor_ver = 'autobahn'
    else:
        # could not detect XOR masker type/version
        xor_ver = '?'
    if debug:
        xor_ver += " [%s]" % qual(XorMaskerNull)

    # JSON Serializer
    #
    from autobahn.wamp.serializer import JsonObjectSerializer
    s = str(JsonObjectSerializer.JSON_MODULE)
    if 'ujson' in s:
        json_ver = 'ujson-%s' % pkg_resources.require('ujson')[0].version
    else:
        json_ver = 'stdlib'

    # MsgPack Serializer
    #
    try:
        import msgpack  # noqa
        msgpack_ver = 'msgpack-python-%s' % pkg_resources.require('msgpack-python')[0].version
    except ImportError:
        msgpack_ver = '-'


    def decorate(text):
        return click.style(text, fg='yellow', bold=True)

    for line in BANNER.splitlines():
        print(decorate("{:>40}".format(line)))

    print(" Crossbar.io        : {0}".format(decorate(crossbar.__version__)))
    print("   Autobahn         : {0}".format(decorate(ab_ver)))
    print("     UTF8 Validator : {0}".format(decorate(utf8_ver)))
    print("     XOR Masker     : {0}".format(decorate(xor_ver)))
    print("     JSON Codec     : {0}".format(decorate(json_ver)))
    print("     MsgPack Codec  : {0}".format(decorate(msgpack_ver)))
    print("   Twisted          : {0}".format(decorate(tx_ver)))
    print("   Python           : {0}/{1}".format(decorate(py_ver),
                                                 decorate(py_ver_detail)))
    print(" OS                 : {0}".format(decorate(platform.platform())))
    print(" Machine            : {0}".format(decorate(platform.machine())))
    print("")


def run_command_templates(options, **kwargs):
    """
    Subcommand "crossbar templates".
    """
    from crossbar.controller.template import Templates

    templates = Templates()
    templates.help()


def run_command_init(options, **kwargs):
    """
    Subcommand "crossbar init".
    """
    log = make_logger()

    from crossbar.controller.template import Templates

    templates = Templates()

    if options.template not in templates:
        log.info("Huh, sorry. There is no template named '{options.template}'. Try 'crossbar templates' to list the templates available.",
                 options=options)
        sys.exit(1)

    if options.appdir is None:
        options.appdir = '.'
    else:
        if os.path.exists(options.appdir):
            raise Exception("app directory '{}' already exists".format(options.appdir))

        try:
            os.mkdir(options.appdir)
        except Exception as e:
            raise Exception("could not create application directory '{}' ({})".format(options.appdir, e))
        else:
            log.info("Crossbar.io application directory '{options.appdir}' created",
                     options=options)

    options.appdir = os.path.abspath(options.appdir)

    log.info("Initializing application template '{options.template}' in directory '{options.appdir}'",
             options=options)
    get_started_hint = templates.init(options.appdir, options.template)

    log.info("Application template initialized")

    if get_started_hint:
        log.info("\n{}\n".format(get_started_hint))
    else:
        log.info("\nTo start your node, run 'crossbar start --cbdir {cbdir}'\n",
                 cbdir=os.path.abspath(os.path.join(options.appdir, '.crossbar')))


def run_command_status(options, **kwargs):
    """
    Subcommand "crossbar status".
    """
    log = make_logger()

    # check if there is a Crossbar.io instance currently running from
    # the Crossbar.io node directory at all
    #
    pid_data = check_is_running(options.cbdir)
    if pid_data is None:
        # https://docs.python.org/2/library/os.html#os.EX_UNAVAILABLE
        # https://www.freebsd.org/cgi/man.cgi?query=sysexits&sektion=3
        log.info("No Crossbar.io instance is currently running from node directory {cbdir}.",
                 cbdir=options.cbdir)
        sys.exit(getattr(os, 'EX_UNAVAILABLE', 1))
    else:
        log.info("A Crossbar.io instance is running from node directory {cbdir} (PID {pid}).",
                 cbdir=options.cbdir, pid=pid_data['pid'])
        sys.exit(0)


def run_command_stop(options, exit=True, **kwargs):
    """
    Subcommand "crossbar stop".
    """
    # check if there is a Crossbar.io instance currently running from
    # the Crossbar.io node directory at all
    #
    pid_data = check_is_running(options.cbdir)
    if pid_data:
        pid = pid_data['pid']
        print("Stopping Crossbar.io currently running from node directory {} (PID {}) ...".format(options.cbdir, pid))
        if not _HAS_PSUTIL:
            os.kill(pid, signal.SIGINT)
            print("SIGINT sent to process {}.".format(pid))
        else:
            p = psutil.Process(pid)
            try:
                # first try to terminate (orderly shutdown)
                _TERMINATE_TIMEOUT = 5
                p.terminate()
                print("SIGINT sent to process {} .. waiting for exit ({} seconds) ...".format(pid, _TERMINATE_TIMEOUT))
                p.wait(timeout=_TERMINATE_TIMEOUT)
            except psutil.TimeoutExpired:
                print("... process {} still alive - will kill now.".format(pid))
                p.kill()
                print("SIGKILL sent to process {}.".format(pid))
            finally:
                print("Process {} terminated.".format(pid))
        if exit:
            sys.exit(0)
        else:
            return pid_data
    else:
        print("No Crossbar.io is currently running from node directory {}.".format(options.cbdir))
        sys.exit(getattr(os, 'EX_UNAVAILABLE', 1))


def _startlog(options):
    """
    Start the logging in a way that all the subcommands can use it.
    """
    from crossbar._logging import log_publisher, start_logging
    from crossbar._logging import set_global_log_level

    loglevel = getattr(options, "loglevel", "info")
    logformat = getattr(options, "logformat", "none")

    set_global_log_level(loglevel)

    if getattr(options, "logtofile", False):
        # We want to log to a file
        from crossbar._logging import make_logfile_observer

        if not options.logdir:
            logdir = options.cbdir
        else:
            logdir = options.logdir

        logfile = os.path.join(logdir, "node.log")

        if loglevel in ["error", "warn", "info"]:
            show_source = False
        else:
            show_source = True

        log_publisher.addObserver(make_logfile_observer(logfile, show_source))
    else:
        # We want to log to stdout/stderr.
        from crossbar._logging import make_stdout_observer
        from crossbar._logging import make_stderr_observer

        if loglevel == "none":
            # Do no logging!
            pass
        elif loglevel in ["error", "warn", "info"]:
            # Print info to stdout, warn+ to stderr
            log_publisher.addObserver(make_stdout_observer(show_source=False,
                                                           format=logformat))
            log_publisher.addObserver(make_stderr_observer(show_source=False,
                                                           format=logformat))
        elif loglevel == "debug":
            # Print debug+info to stdout, warn+ to stderr, with the class
            # source
            log_publisher.addObserver(make_stdout_observer(show_source=True,
                                                           format=logformat))
            log_publisher.addObserver(make_stderr_observer(show_source=True,
                                                           format=logformat))
        elif loglevel == "trace":
            # Print trace+, with the class source
            log_publisher.addObserver(make_stdout_observer(show_source=True,
                                                           format=logformat,
                                                           trace=True))
            log_publisher.addObserver(make_stderr_observer(show_source=True,
                                                           format=logformat))
        else:
            assert False, "Shouldn't ever get here."

    # Actually start the logger.
    start_logging()


def run_command_start(options, reactor=None):
    """
    Subcommand "crossbar start".
    """
    # do not allow to run more than one Crossbar.io instance
    # from the same Crossbar.io node directory
    #
    pid_data = check_is_running(options.cbdir)
    if pid_data:
        print("Crossbar.io is already running from node directory {} (PID {}).".format(options.cbdir, pid_data['pid']))
        sys.exit(1)
    else:
        fp = os.path.join(options.cbdir, _PID_FILENAME)
        with open(fp, 'w') as fd:
            argv = options.argv
            options_dump = vars(options)
            pid_data = {
                'pid': os.getpid(),
                'argv': argv,
                'options': {x: y for x, y in options_dump.items()
                            if x not in ["func", "argv"]}
            }
            fd.write("{}\n".format(json.dumps(pid_data, sort_keys=False, indent=3, separators=(',', ': '))))

    # remove node PID file when reactor exits
    #
    def remove_pid_file():
        fp = os.path.join(options.cbdir, _PID_FILENAME)
        if os.path.isfile(fp):
            os.remove(fp)
    reactor.addSystemEventTrigger('after', 'shutdown', remove_pid_file)

    log = make_logger()

    # Print the banner.
    for line in BANNER.splitlines():
        log.info(click.style(("{:>40}").format(line), fg='yellow', bold=True))

    bannerFormat = "{:>12} {:<24}"
    log.info(bannerFormat.format("Version:", click.style(crossbar.__version__, fg='yellow', bold=True)))
    # log.info(bannerFormat.format("Python:", click.style(platform.python_implementation(), fg='yellow', bold=True)))
    # log.info(bannerFormat.format("Reactor:", click.style(qual(reactor.__class__).split('.')[-1], fg='yellow', bold=True)))
    log.info(bannerFormat.format("Started:", click.style(utcnow(), fg='yellow', bold=True)))
    log.info()

    log.info("Starting from node directory {}".format(options.cbdir))

    from crossbar.controller.node import Node
    from crossbar.common.checkconfig import InvalidConfigException
    node = Node(reactor, options)

    try:
        node.check_config()
    except InvalidConfigException as e:
        log.error("*** Configuration validation failed ***")
        log.error("{e!s}", e=e)
        sys.exit(1)
    except:
        raise

    def start_crossbar():
        """
        Start the crossbar node.
        """
        d = node.start()

        def on_error(err):
            log.error("Could not start node: {error}", error=err.value)
            if reactor.running:
                reactor.stop()
        d.addErrback(on_error)
    reactor.callWhenRunning(start_crossbar)

    try:
        log.info("Entering reactor event loop...")
        reactor.run()
    except Exception:
        log.failure("Could not start reactor: {log_failure.value}")


def run_command_restart(options, **kwargs):
    """
    Subcommand "crossbar restart".
    """
    pid_data = run_command_stop(options, exit=False)
    prog = pid_data['argv'][0]
    # remove first item, which is the (fully qualified) path to Python
    args = pid_data['argv'][1:]
    # replace 'restart' with 'start'
    args = [(lambda x: x if x != 'restart' else 'start')(x) for x in args]
    run(prog, args)


def run_command_check(options, **kwargs):
    """
    Subcommand "crossbar check".
    """
    from crossbar.common.checkconfig import check_config_file
    configfile = os.path.join(options.cbdir, options.config)

    print("Checking local configuration file {}".format(configfile))

    try:
        check_config_file(configfile)
    except Exception as e:
        print("\nError: {}\n".format(e))
        sys.exit(1)
    else:
        print("Ok, configuration file looks good.")
        sys.exit(0)


def run_command_convert(options, **kwargs):
    """
    Subcommand "crossbar convert".
    """
    from crossbar.common.checkconfig import convert_config_file
    configfile = os.path.join(options.cbdir, options.config)

    print("Converting local configuration file {}".format(configfile))

    try:
        convert_config_file(configfile)
    except Exception as e:
        print("\nError: {}\n".format(e))
        sys.exit(1)
    else:
        sys.exit(0)


def run(prog=None, args=None, reactor=None):
    """
    Entry point of Crossbar.io CLI.
    """

    loglevel_args = {
        "type": str,
        "default": 'info',
        "choices": ['none', 'error', 'warn', 'info', 'debug', 'trace'],
        "help": ("How much Crossbar.io should log to the terminal, in order "
                 "of verbosity.")
    }

    # create the top-level parser
    #
    parser = argparse.ArgumentParser(prog='crossbar',
                                     description="Crossbar.io - Polyglot application router - http://crossbar.io")

    # top-level options
    #
    parser.add_argument('--reactor',
                        default=None,
                        choices=['select', 'poll', 'epoll', 'kqueue', 'iocp'],
                        help='Explicit Twisted reactor selection')

    # create subcommand parser
    #
    subparsers = parser.add_subparsers(dest='command',
                                       title='commands',
                                       help='Crossbar.io command to run')

    # "version" command
    #
    parser_version = subparsers.add_parser('version',
                                           help='Print software versions.')

    parser_version.add_argument('--loglevel',
                                **loglevel_args)

    parser_version.set_defaults(func=run_command_version)

    # "init" command
    #
    parser_init = subparsers.add_parser('init',
                                        help='Initialize a new Crossbar.io node.')

    parser_init.set_defaults(func=run_command_init)

    parser_init.add_argument('--template',
                             type=str,
                             default='default',
                             help="Template for initialization")

    parser_init.add_argument('--appdir',
                             type=str,
                             default=None,
                             help="Application base directory where to create app and node from template.")

    # "templates" command
    #
    parser_templates = subparsers.add_parser('templates',
                                             help='List templates available for initializing a new Crossbar.io node.')

    parser_templates.set_defaults(func=run_command_templates)

    # "start" command
    #
    parser_start = subparsers.add_parser('start',
                                         help='Start a Crossbar.io node.')

    parser_start.set_defaults(func=run_command_start)

    parser_start.add_argument('--cbdir',
                              type=str,
                              default=None,
                              help="Crossbar.io node directory (overrides ${CROSSBAR_DIR} and the default ./.crossbar)")

    parser_start.add_argument('--config',
                              type=str,
                              default=None,
                              help="Crossbar.io configuration file (overrides default CBDIR/config.json)")

    parser_start.add_argument('--logdir',
                              type=str,
                              default=None,
                              help="Crossbar.io log directory (default: <Crossbar Node Directory>/)")

    parser_start.add_argument('--logtofile',
                              action='store_true',
                              help="Whether or not to log to file")

    parser_start.add_argument('--loglevel',
                              **loglevel_args)

    parser_start.add_argument('--logformat',
                              type=str,
                              default='colour',
                              choices=['syslogd', 'nocolour', 'colour'],
                              help="The format of the logs -- suitable for syslogd, not coloured, or coloured.")

    # "stop" command
    #
    parser_stop = subparsers.add_parser('stop',
                                        help='Stop a Crossbar.io node.')

    parser_stop.add_argument('--cbdir',
                             type=str,
                             default=None,
                             help="Crossbar.io node directory (overrides ${CROSSBAR_DIR} and the default ./.crossbar)")

    parser_stop.set_defaults(func=run_command_stop)

    # "restart" command
    #
    parser_restart = subparsers.add_parser('restart',
                                           help='Restart a Crossbar.io node.')

    parser_restart.add_argument('--cbdir',
                                type=str,
                                default=None,
                                help="Crossbar.io node directory (overrides ${CROSSBAR_DIR} and the default ./.crossbar)")

    parser_restart.set_defaults(func=run_command_restart)

    # "status" command
    #
    parser_status = subparsers.add_parser('status',
                                          help='Checks whether a Crossbar.io node is running.')

    parser_status.add_argument('--cbdir',
                               type=str,
                               default=None,
                               help="Crossbar.io node directory (overrides ${CROSSBAR_DIR} and the default ./.crossbar)")

    parser_status.set_defaults(func=run_command_status)

    # "check" command
    #
    parser_check = subparsers.add_parser('check',
                                         help='Check a Crossbar.io node`s local configuration file.')

    parser_check.set_defaults(func=run_command_check)

    parser_check.add_argument('--cbdir',
                              type=str,
                              default=None,
                              help="Crossbar.io node directory (overrides ${CROSSBAR_DIR} and the default ./.crossbar)")

    parser_check.add_argument('--config',
                              type=str,
                              default=None,
                              help="Crossbar.io configuration file (overrides default CBDIR/config.json)")

    # "convert" command
    #
    parser_check = subparsers.add_parser('convert',
                                         help='Convert a Crossbar.io node`s local configuration file from JSON to YAML or vice versa.')

    parser_check.set_defaults(func=run_command_convert)

    parser_check.add_argument('--cbdir',
                              type=str,
                              default=None,
                              help="Crossbar.io node directory (overrides ${CROSSBAR_DIR} and the default ./.crossbar)")

    parser_check.add_argument('--config',
                              type=str,
                              default=None,
                              help="Crossbar.io configuration file (overrides default CBDIR/config.json)")

    # parse cmd line args
    #
    options = parser.parse_args(args)
    if args:
        options.argv = [prog] + args
    else:
        options.argv = sys.argv

    # Crossbar.io node directory
    #
    if hasattr(options, 'cbdir'):
        if not options.cbdir:
            if "CROSSBAR_DIR" in os.environ:
                options.cbdir = os.environ['CROSSBAR_DIR']
            else:
                options.cbdir = '.crossbar'
        options.cbdir = os.path.abspath(options.cbdir)

    # Crossbar.io node configuration file
    #
    if hasattr(options, 'config'):
        if not options.config:
            for f in ['config.json', 'config.yaml']:
                f = os.path.join(options.cbdir, f)
                if os.path.isfile(f) and os.access(f, os.R_OK):
                    options.config = f
                    break
            if not options.config:
                raise Exception("No config file specified, and neither CBDIR/config.json nor CBDIR/config.yaml exists")
        else:
            options.config = os.path.join(options.cbdir, options.config)

    # Log directory
    #
    if hasattr(options, 'logdir'):
        if options.logdir:
            options.logdir = os.path.abspath(os.path.join(options.cbdir, options.logdir))
            if not os.path.isdir(options.logdir):
                try:
                    os.mkdir(options.logdir)
                except Exception as e:
                    print("Could not create log directory: {}".format(e))
                    sys.exit(1)

    # Start the logger
    _startlog(options)


    if not reactor:
        # try and get the log verboseness we want -- not all commands have a
        # loglevel, so just default to info in that case
        debug = getattr(options, "loglevel", "info") in ("debug", "trace")

        # we use an Autobahn utility to import the "best" available Twisted
        # reactor
        reactor = install_reactor(options.reactor, debug)

    # run the subcommand selected
    #
    options.func(options, reactor=reactor)


if __name__ == '__main__':
    try:
        run()
    except Exception as e:
        print("\nError: {}\n".format(e))
        traceback.print_exc()
        sys.exit(1)
    else:
        sys.exit(0)
