#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
    nodeenv
    ~~~~~~~
    Node.js virtual environment

    :copyright: (c) 2011 by Eugene Kalinin
    :license: BSD, see LICENSE for more details.
"""

nodeenv_version = '0.6.6'

import sys
import os
import stat
import logging
import optparse
import subprocess
import pipes
import re
import tempfile
import zipfile
import shutil
from distutils.dir_util import copy_tree

try:
    import ConfigParser
except ImportError:
    # Python 3
    import configparser as ConfigParser

try:
    import urllib.request as urllib
except ImportError:
    # Python 2.x
    import urllib2 as urllib

try:
    from urllib.error import HTTPError
except ImportError:
    # Python 2.x
    from urllib2 import HTTPError

from pkg_resources import parse_version

join = os.path.join
abspath = os.path.abspath

is_windows_nt = os.name == 'nt'

# ---------------------------------------------------------
# Utils


def create_logger():
    """
    Create logger for diagnostic
    """
    # create logger
    logger = logging.getLogger("nodeenv")
    logger.setLevel(logging.INFO)

    # monkey patch
    def emit(self, record):
        msg = self.format(record)
        fs = "%s" if getattr(record, "continued", False) else "%s\n"
        self.stream.write(fs % msg)
        self.flush()
    logging.StreamHandler.emit = emit

    # create console handler and set level to debug
    ch = logging.StreamHandler()
    ch.setLevel(logging.DEBUG)

    # create formatter
    formatter = logging.Formatter(fmt="%(message)s")

    # add formatter to ch
    ch.setFormatter(formatter)

    # add ch to logger
    logger.addHandler(ch)
    return logger
logger = create_logger()


def parse_args():
    """
    Parses command line arguments
    """
    parser = optparse.OptionParser(
        version=nodeenv_version,
        usage="%prog [OPTIONS] ENV_DIR")

    parser.add_option('-n', '--node', dest='node',
        metavar='NODE_VER', default=None,
        help='The node.js version to use, e.g., '
        '--node=0.4.3 will use the node-v0.4.3 '
        'to create the new environment. The default is last stable version. '
        'Use `system` to use system-wide node.')

    parser.add_option('-j', '--jobs', dest='jobs', default='2',
        help='Sets number of parallel commands at node.js compilation. '
        'The default is 2 jobs.')

    parser.add_option('--load-average', dest='load_average',
        help='Sets maximum load average for executing parallel commands at node.js compilation.')

    parser.add_option('-v', '--verbose',
        action='store_true', dest='verbose', default=False,
        help="Verbose mode")

    parser.add_option('-q', '--quiet',
        action='store_true', dest='quiet', default=False,
        help="Quiet mode")

    parser.add_option('-r', '--requirements',
        dest='requirements', default='', metavar='FILENAME',
        help='Install all the packages listed in the given requirements file.')

    parser.add_option('--prompt', dest='prompt',
        help='Provides an alternative prompt prefix for this environment')

    parser.add_option('-l', '--list', dest='list',
        action='store_true', default=False,
        help='Lists available node.js versions')

    parser.add_option('--without-ssl', dest='without_ssl',
        action='store_true', default=False,
        help='Build node.js without SSL support')

    parser.add_option('--debug', dest='debug',
        action='store_true', default=False,
        help='Build debug variant of the node.js')

    parser.add_option('--profile', dest='profile',
        action='store_true', default=False,
        help='Enable profiling for node.js')

    parser.add_option('--with-npm', dest='with_npm',
        action='store_true', default=False,
        help='Build without installing npm into the new virtual environment. '
        'Required for node.js < 0.6.3. By default, the npm included with node.js is used.')

    parser.add_option('--npm', dest='npm',
        metavar='NPM_VER', default='latest',
        help='The npm version to use, e.g., '
        '--npm=0.3.18 will use the npm-0.3.18.tgz '
        'tarball to install. The default is last available version.')

    parser.add_option('--no-npm-clean', dest='no_npm_clean',
        action='store_true', default=False,
        help='Skip the npm 0.x cleanup.  Cleanup is enabled by default.')

    parser.add_option('--python-virtualenv', '-p', dest='python_virtualenv',
        action='store_true', default=False,
        help='Use current python virtualenv')

    parser.add_option('--clean-src', '-c', dest='clean_src',
        action='store_true', default=False,
        help='Remove "src" directory after installation')

    parser.add_option('--force', dest='force',
        action='store_true', default=False,
        help='Force installation in a pre-existing directory')

    options, args = parser.parse_args()

    if not options.list and not options.python_virtualenv:
        if not args:
            print('You must provide a DEST_DIR or use current python virtualenv')
            parser.print_help()
            sys.exit(2)

        if len(args) > 1:
            print('There must be only one argument: DEST_DIR (you gave %s)' % (
                ' '.join(args)))
            parser.print_help()
            sys.exit(2)

    return options, args


def mkdir(path):
    """
    Create directory
    """
    if not os.path.exists(path):
        logger.debug(' * Creating: %s ... ', path, extra=dict(continued=True))
        os.makedirs(path)
        logger.debug('done.')
    else:
        logger.debug(' * Directory %s already exists', path)

def get_bin_dir(opt, env_dir=None):
    """
    Returns the bin directory path. If env_dir is None, the path returned will
    be relative to the env_dir.
    """
    bin_dir = 'bin'

    if is_windows_nt:
        # Python virtualenv on Windows prefers the Scripts directory for
        # executables instead of the bin directory
        if opt.python_virtualenv:
            bin_dir = 'Scripts'

    if env_dir:
        bin_dir = join(env_dir, bin_dir)

    return bin_dir

def get_mod_dir(opt, env_dir=None):
    """
    Returns the path to the global node_modules directory, relative to the
    env root unless env_dir is given.
    """
    if is_windows_nt:
        mod_dir = join(get_bin_dir(opt), 'node_modules')
    else:
        mod_dir = join('lib', 'node_modules')

    if env_dir:
        mod_dir = join(env_dir, mod_dir)

    return mod_dir

def writefile(dest, content, overwrite=True, append=False):
    """
    Create file and write content in it
    """
    if not os.path.exists(dest):
        logger.debug(' * Writing %s ... ', dest, extra=dict(continued=True))
        f = open(dest, 'wb')
        f.write(content.encode('utf-8'))
        f.close()
        logger.debug('done.')
        return
    else:
        f = open(dest, 'rb')
        c = f.read()
        f.close()
        if c != content:
            if not overwrite:
                logger.info(' * File %s exists with different content; not overwriting', dest)
                return
            if append:
                logger.info(' * Appending nodeenv settings to %s', dest)
                f = open(dest, 'ab')
                f.write(DISABLE_PROMPT.encode('utf-8'))
                f.write(content.encode('utf-8'))
                f.write(ENABLE_PROMPT.encode('utf-8'))
                f.close()
                return
            logger.info(' * Overwriting %s with new content', dest)
            f = open(dest, 'wb')
            f.write(content.encode('utf-8'))
            f.close()
        else:
            logger.debug(' * Content %s already in place', dest)


def callit(cmd, show_stdout=True, in_shell=False,
        cwd=None, extra_env=None):
    """
    Execute cmd line in sub-shell
    """
    all_output = []
    cmd_parts = []

    for part in cmd:
        if len(part) > 45:
            part = part[:20] + "..." + part[-20:]
        if ' ' in part or '\n' in part or '"' in part or "'" in part:
            part = '"%s"' % part.replace('"', '\\"')
        cmd_parts.append(part)
    cmd_desc = ' '.join(cmd_parts)
    logger.debug(" ** Running command %s" % cmd_desc)

    if in_shell:
        cmd = ' '.join(cmd)

    # output
    stdout = subprocess.PIPE

    # env
    if extra_env:
        env = os.environ.copy()
        if extra_env:
            env.update(extra_env)
    else:
        env = None

    # execute
    try:
        proc = subprocess.Popen(
            cmd, stderr=subprocess.STDOUT, stdin=None, stdout=stdout,
            cwd=cwd, env=env, shell=in_shell)
    except Exception:
        e = sys.exc_info()[1]
        logger.error("Error %s while executing command %s" % (e, cmd_desc))
        raise

    stdout = proc.stdout
    while stdout:
        line = stdout.readline()
        if not line:
            break
        line = line.rstrip()
        all_output.append(line)
        if show_stdout:
            logger.info(line)
    proc.wait()

    # error handler
    if proc.returncode:
        if show_stdout:
            for s in all_output:
                logger.critical(s)
        raise OSError("Command %s failed with error code %s"
            % (cmd_desc, proc.returncode))

    return proc.returncode, all_output


def get_node_src_url(version, postfix=''):
    node_name = 'node-v%s%s' % (version, postfix)
    tar_name = '%s.tar.gz' % (node_name)
    if parse_version(version) > parse_version("0.5.0"):
        node_url = 'http://nodejs.org/dist/v%s/%s' % (version, tar_name)
    else:
        node_url = 'http://nodejs.org/dist/%s' % (tar_name)
    return node_url

def download_node_win(dest_dir, opt):
    """
    Download the Windows node binary.
    """
    # platform.machine() is better but not available in Python < 2.7
    is_x64 = 'PROGRAMFILES(X86)' in os.environ

    node_url = 'http://nodejs.org/dist/v{0}/'.format(opt.node)
    if is_x64:
        node_url += 'x64/'
    node_url += 'node.exe'

    r = None
    try:
        r = urllib.urlopen(node_url)
        with open(join(dest_dir, 'node-venv.exe'), 'wb') as f:
            f.write(r.read())
    except HTTPError:
        logger.error('The requested version of node does not exist for Windows. '
                     'Use the -l option to see available versions.')
        raise
    finally:
        if r: r.close()


def download_node(node_url, src_dir, env_dir, opt):
    """
    Download source code
    """
    if is_windows_nt:
        raise NotImplementedError('Downloading the node source code is not '
                                  'supported on Windows.')

    cmd = []
    cmd.append('curl')
    cmd.append('--silent')
    cmd.append('-L')
    cmd.append(node_url)
    cmd.append('|')
    cmd.append('tar')
    cmd.append('xzf')
    cmd.append('-')
    cmd.append('-C')
    cmd.append(pipes.quote(src_dir))
    try:
        callit(cmd, opt.verbose, True, env_dir)
        logger.info(') ', extra=dict(continued=True))
    except OSError:
        postfix = '-RC1'
        logger.info('%s) ' % postfix, extra=dict(continued=True))
        new_node_url = get_node_src_url(opt.node, postfix)
        cmd[cmd.index(node_url)] = new_node_url
        callit(cmd, opt.verbose, True, env_dir)

# ---------------------------------------------------------
# Virtual environment functions

def install_node_win(env_dir, opt):
    """
    Download the pre-compiled node binary and install it into the virtual
    environment.
    """
    logger.info(' * Installing node.js (%s)... ' % opt.node,
                         extra=dict(continued=True))

    bin_dir = get_bin_dir(opt, env_dir)

    node_exe_path = join(bin_dir, 'node-venv.exe')
    if os.path.exists(node_exe_path):
        proc = subprocess.Popen((node_exe_path, '-v'), stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        stdout, stderr = proc.communicate()

        cur_ver = stdout.decode('utf-8').strip()
        if cur_ver[0] == 'v':
            cur_ver = cur_ver[1:]
        if cur_ver == opt.node:
            logger.info('requested version already installed.')
            return

    download_node_win(bin_dir, opt)
    logger.info(' done.')

def install_node(env_dir, src_dir, opt):
    """
    Download source code for node.js, unpack it
    and install it in virtual environment.
    """
    if is_windows_nt:
        return install_node_win(env_dir, opt)

    logger.info(' * Install node.js (%s' % opt.node,
                         extra=dict(continued=True))

    node_name = 'node-v%s' % (opt.node)
    tar_name = '%s.tar.gz' % (node_name)
    node_url = get_node_src_url(opt.node)
    node_tar = join(src_dir, tar_name)
    node_src_dir = join(src_dir, node_name)
    env_dir = abspath(env_dir)
    old_chdir = os.getcwd()

    # get src if not downloaded yet
    if not os.path.exists(node_src_dir):
        download_node(node_url, src_dir, env_dir, opt)

    logger.info('.', extra=dict(continued=True))

    env = {}
    make_param_names = ['load-average', 'jobs']
    make_param_values = map(lambda x: getattr(opt, x.replace('-','_')), make_param_names)
    make_opts = [ '--{0}={1}'.format(name, value)
                  if len(value) > 0 else '--{0}'.format(name)
                  for name, value in zip(make_param_names, make_param_values)
                  if value is not None ]

    conf_cmd = []
    conf_cmd.append('./configure')
    conf_cmd.append('--prefix=%s' % pipes.quote(env_dir))
    if opt.without_ssl:
        conf_cmd.append('--without-ssl')
    if opt.debug:
        conf_cmd.append('--debug')
    if opt.profile:
        conf_cmd.append('--profile')

    callit(conf_cmd, opt.verbose, True, node_src_dir, env)
    logger.info('.', extra=dict(continued=True))
    callit(['make']+make_opts, opt.verbose, True, node_src_dir, env)
    logger.info('.', extra=dict(continued=True))
    callit(['make install'], opt.verbose, True, node_src_dir, env)

    logger.info(' done.')

def install_npm_win(env_dir, opt):
    """
    Download source code for npm, unpack it and install it in virtual
    environment.
    """
    logger.info(' * Installing npm.js (%s) ... ' % opt.npm,
                    extra=dict(continued=True))

    install_ver = opt.npm
    if install_ver == 'latest':
        r = None
        try:
            r = urllib.urlopen('http://nodejs.org/dist/npm/')
            npm_dist_html = r.read().decode('utf-8')
        finally:
            if r: r.close()

        A_HREF_RE = re.compile(r'<a href="npm-([\w\.\-]+)\.zip">')
        versions = [ (m.group(1), parse_version(m.group(1))) for m in A_HREF_RE.finditer(npm_dist_html) ]
        versions.sort(key=lambda v: v[1])
        install_ver = versions[-1][0]
        logger.info('installing v{0}... '.format(install_ver), extra=dict(continued=True))

    bin_dir = get_bin_dir(opt, env_dir)
    mod_dir = get_mod_dir(opt)

    npm_src_zip_file_path = tempfile.mkstemp(dir=env_dir)
    os.close(npm_src_zip_file_path[0])
    npm_src_zip_file_path = npm_src_zip_file_path[1]
    npm_src_dir = tempfile.mkdtemp(dir=env_dir)
    r = None
    try:
        r = urllib.urlopen('http://nodejs.org/dist/npm/npm-{0}.zip'.format(install_ver))
        with open(npm_src_zip_file_path, 'wb') as f:
            f.write(r.read())

        with zipfile.ZipFile(npm_src_zip_file_path) as npm_src_zip:
            npm_src_zip.extractall(npm_src_dir)

        copy_tree(join(npm_src_dir, 'node_modules'), join(env_dir, mod_dir))

        for f in os.listdir(npm_src_dir):
            if f.lower().endswith('.cmd'):
                shutil.copy(join(npm_src_dir, f), bin_dir)

    finally:
        if r: r.close()
        os.remove(npm_src_zip_file_path)
        shutil.rmtree(npm_src_dir, ignore_errors=True)

    logger.info('done.')


def install_npm(env_dir, src_dir, opt):
    """
    Download source code for npm, unpack it
    and install it in virtual environment.
    """
    if is_windows_nt:
        return install_npm_win(env_dir, opt)

    logger.info(' * Install npm.js (%s) ... ' % opt.npm,
                    extra=dict(continued=True))
    cmd = ['. %s && curl --silent %s | clean=%s npm_install=%s bash && deactivate_node' % (
            pipes.quote(join(env_dir, 'bin', 'activate')),
            'https://npmjs.org/install.sh',
            'no' if opt.no_npm_clean else 'yes',
            opt.npm)]
    callit(cmd, opt.verbose, True)
    logger.info('done.')

def install_packages_win(env_dir, opt):
    """
    Install node.js packages using npm.
    """
    logger.info(' * Installing node.js packages ... ',
        extra=dict(continued=True))
    with open(opt.requirements, 'r') as f:
        packages = [ package.strip() for package in f ]
    real_npm_ver = opt.npm if opt.npm.count(".") == 2 else opt.npm + ".0"
    if opt.npm == "latest" or real_npm_ver >= "1.0.0":
        for p in packages:
            retcode = subprocess.call(['npm', '-g', 'install', p], shell=True)
            if retcode: logger.error('Could not install {0}.'.format(p))
    else:
        for p in packages:
            retcode = subprocess.call(['npm', '-g', 'install', p], shell=True)
            if retcode: logger.error('Could not install {0}.'.format(p))
            retcode = subprocess.call(['npm', '-g', 'activate', p], shell=True)
            if retcode: logger.error('Could not activate {0}.'.format(p))

    logger.info('done.')

def install_packages(env_dir, opt):
    """
    Install node.js packages via npm
    """
    if is_windows_nt:
        return install_packages_win(env_dir, opt)

    logger.info(' * Install node.js packages ... ',
        extra=dict(continued=True))
    packages = [package.strip() for package in
                    open(opt.requirements).readlines()]
    activate_path = join(env_dir, 'bin', 'activate')
    real_npm_ver = opt.npm if opt.npm.count(".") == 2 else opt.npm + ".0"
    if opt.npm == "latest" or real_npm_ver >= "1.0.0":
        cmd = '. ' + pipes.quote(activate_path) + \
                ' && npm install -g %(pack)s'
    else:
        cmd = '. ' + pipes.quote(activate_path) + \
                ' && npm install %(pack)s' + \
                ' && npm activate %(pack)s'

    for package in packages:
        callit(cmd=[cmd % {"pack": package}],
                show_stdout=opt.verbose, in_shell=True)

    logger.info('done.')


def install_activate(env_dir, opt):
    """
    Install virtual environment activation script
    """
    files = {
        'activate': ACTIVATE_SH
    }
    if is_windows_nt:
        files = {
            'node.bat' : NODE_BAT
        }

    rel_bin_dir = get_bin_dir(opt)
    rel_mod_dir = get_mod_dir(opt)
    prompt = opt.prompt or '(%s)' % os.path.basename(os.path.abspath(env_dir))
    mode_0755 = stat.S_IRWXU | stat.S_IXGRP | stat.S_IRGRP | stat.S_IROTH | stat.S_IXOTH

    for name, content in files.items():
        file_path = join(env_dir, rel_bin_dir, name)
        content = content.replace('__NODE_VIRTUAL_PROMPT__', prompt)
        content = content.replace('__NODE_VIRTUAL_ENV__', os.path.abspath(env_dir))
        content = content.replace('__BIN_NAME__', rel_bin_dir)
        content = content.replace('__MOD_NAME__', rel_mod_dir)
        writefile(file_path, content, append=(opt.python_virtualenv and not is_windows_nt))
        os.chmod(file_path, mode_0755)


def create_environment(env_dir, opt):
    """
    Creates a new environment in ``env_dir``.
    """
    if os.path.exists(env_dir) and not opt.python_virtualenv:
        logger.info(' * Environment already exists: %s', env_dir)
        if not opt.force:
            sys.exit(2)
    if is_windows_nt:
        src_dir = None
    else:
        src_dir = abspath(join(env_dir, 'src'))
        mkdir(src_dir)
    save_env_options(env_dir, opt)

    if opt.node is None:
        opt.node = get_last_stable_node_version()
    if opt.node != "system":
        install_node(env_dir, src_dir, opt)
    else:
        if not is_windows_nt:
            mkdir(get_bin_dir(opt, env_dir))
            mkdir(join(env_dir, 'lib'))
        mkdir(get_mod_dir(opt, env_dir))

    # activate script install must be
    # before npm install, npm use activate
    # for install
    install_activate(env_dir, opt)
    if parse_version(opt.node) < parse_version("0.6.3") or opt.with_npm or is_windows_nt:
        install_npm(env_dir, src_dir, opt)
    if opt.requirements:
        install_packages(env_dir, opt)
    # Cleanup
    if opt.clean_src and not is_windows_nt:
        callit(['rm -rf', pipes.quote(src_dir)], opt.verbose, True, env_dir)

def print_node_versions_win():
    """
    Prints into stdout all available node.js versions for Windows.
    """
    r = None
    try:
        r = urllib.urlopen('http://nodejs.org/dist/')
        dist_html = r.read().decode("utf-8")
    finally:
        if r: r.close()

    A_HREF_RE = re.compile(r'<a href="v([\w\.\-]+)/">')
    versions = [ (m.group(1), parse_version(m.group(1))) for m in A_HREF_RE.finditer(dist_html) ]
    versions.sort(key=lambda v: v[1])

    pos = 0
    rowx = [ ]
    for ver in versions:
        pos += 1
        rowx.append(ver[0])
        if pos % 8 == 0:
            logger.info('\t'.join(rowx))
            rowx = []
    logger.info('\t'.join(rowx))

def print_node_versions():
    """
    Prints into stdout all available node.js versions
    """
    if is_windows_nt:
        return print_node_versions_win()

    p = subprocess.Popen(
        "curl -s http://nodejs.org/dist/ | "
        "egrep -o '[0-9]+\.[0-9]+\.[0-9]+' | "
        "sort -u -k 1,1n -k 2,2n -k 3,3n -t . ",
        shell=True, stdout=subprocess.PIPE)
    #out, err = p.communicate()
    pos = 0
    rowx = []
    while 1:
        row = p.stdout.readline().decode('utf-8')
        pos += 1
        if not row:
            logger.info('\t'.join(rowx))
            break
        rowx.append(row.replace('\n', ''))
        if pos % 8 == 0:
            logger.info('\t'.join(rowx))
            rowx = []

def get_last_stable_node_version():
    """
    Return last stable node.js version
    """
    r = None
    try:
        r = urllib.urlopen('http://nodejs.org/dist/latest/')
        latest_html = r.read().decode('utf-8')
    finally:
        if r: r.close()

    TAR_GZ_RE = re.compile(r'node-v([\w\.]+)\.tar\.gz')
    m = TAR_GZ_RE.search(latest_html)
    if m:
        return m.group(1)
    else:
        raise "<unknown>"

def save_env_options(env_dir, opt, file_path='install.cfg'):
    """
    Save command line options into config file
    """
    section_name = 'options'
    config = ConfigParser.RawConfigParser()
    config.add_section(section_name)
    for o, v in opt.__dict__.items():
        config.set(section_name, o, v)

    with open(join(env_dir, file_path), 'w') as configfile:
        config.write(configfile)


def main():
    """
    Entry point
    """
    opt, args = parse_args()

    if opt.list:
        print_node_versions()
        return

    if is_windows_nt:
        if opt.without_ssl:
            raise NotImplementedError('Installing node from source is not '
                                      'supported for Windows, therefore the '
                                      '--without-ssl argument is invalid.')
        if opt.debug:
            raise NotImplementedError('Installing node from source is not '
                                      'supported for Windows, therefore the '
                                      '--debug argument is invalid.')
        if opt.load_average:
            raise NotImplementedError('Installing node from source is not '
                                      'supported for Windows, therefore the '
                                      '--load-average argument is invalid.')
        if opt.clean_src:
            raise NotImplementedError('Installing node from source is not '
                                      'supported for Windows, therefore the '
                                      '--clean-src argument is invalid.')
        if not opt.python_virtualenv:
            raise NotImplementedError('Using nodeenv on Windows is not '
                                      'supported without an existing Python '
                                      'virtualenv.')

    if opt.node != 'system' and sys.version_info.major > 2 and not is_windows_nt:
        logger.error('Python 3.x detected. The node.js build system requires '
                     'Python 2.6-2.7 to build. Python 3 can only be used with '
                     'the system version of node.js; specify the -n \'system\' '
                     'option to use this.')
    else:
        if opt.quiet:
            logger.setLevel(logging.CRITICAL)
        if opt.python_virtualenv:
            try:
                env_dir = os.environ['VIRTUAL_ENV']
            except KeyError:
                logger.error('No python virtualenv is available')
                sys.exit(2)
        else:
            env_dir = args[0]
        create_environment(env_dir, opt)


# ---------------------------------------------------------
# Shell scripts content

DISABLE_PROMPT = """
# disable nodeenv's prompt
# (prompt already changed by original virtualenv's script)
# https://github.com/ekalinin/nodeenv/issues/26
NODE_VIRTUAL_ENV_DISABLE_PROMPT=1
"""

ENABLE_PROMPT = """
unset NODE_VIRTUAL_ENV_DISABLE_PROMPT
"""

ACTIVATE_SH = """

# This file must be used with "source bin/activate" *from bash*
# you cannot run it directly

deactivate_node () {
    # reset old environment variables
    if [ -n "$_OLD_NODE_VIRTUAL_PATH" ] ; then
        PATH="$_OLD_NODE_VIRTUAL_PATH"
        export PATH
        unset _OLD_NODE_VIRTUAL_PATH

        NODE_PATH="$_OLD_NODE_PATH"
        export NODE_PATH
        unset _OLD_NODE_PATH

        NPM_CONFIG_PREFIX="$_OLD_NPM_CONFIG_PREFIX"
        export NPM_CONFIG_PREFIX
        unset _OLD_NPM_CONFIG_PREFIX
    fi

    # This should detect bash and zsh, which have a hash command that must
    # be called to get it to forget past commands.  Without forgetting
    # past commands the $PATH changes we made may not be respected
    if [ -n "$BASH" -o -n "$ZSH_VERSION" ] ; then
        hash -r
    fi

    if [ -n "$_OLD_NODE_VIRTUAL_PS1" ] ; then
        PS1="$_OLD_NODE_VIRTUAL_PS1"
        export PS1
        unset _OLD_NODE_VIRTUAL_PS1
    fi

    unset NODE_VIRTUAL_ENV
    if [ ! "$1" = "nondestructive" ] ; then
    # Self destruct!
        unset -f deactivate_node
    fi
}

freeze () {
    NPM_VER=`npm -v | cut -d '.' -f 1`
    if [ "$NPM_VER" != '1' ]; then
        NPM_LIST=`npm list installed active 2>/dev/null | cut -d ' ' -f 1 | grep -v npm`
    else
        NPM_LIST=`npm ls -g | grep -E '^.{4}\w{1}' | grep -o -E '[a-zA-Z0-9\-]+@[0-9]+\.[0-9]+\.[0-9]+' | grep -v npm`
    fi

    if [ -z "$@" ]; then
        echo "$NPM_LIST"
    else
        echo "$NPM_LIST" > $@
    fi
}

# unset irrelavent variables
deactivate_node nondestructive

# find the directory of this script
# http://stackoverflow.com/a/246128
if [ "${BASH_SOURCE}" ] ; then
    SOURCE="${BASH_SOURCE[0]}"

    while [ -h "$SOURCE" ] ; do SOURCE="$(readlink "$SOURCE")"; done
    DIR="$( cd -P "$( dirname "$SOURCE" )" && pwd )"

    NODE_VIRTUAL_ENV="$(dirname "$DIR")"
else
    # dash not movable. fix use case:
    #   dash -c " . node-env/bin/activate && node -v"
    NODE_VIRTUAL_ENV="__NODE_VIRTUAL_ENV__"
fi

# NODE_VIRTUAL_ENV is the parent of the directory where this script is
export NODE_VIRTUAL_ENV

_OLD_NODE_VIRTUAL_PATH="$PATH"
PATH="$NODE_VIRTUAL_ENV/__BIN_NAME__:$PATH"
export PATH

_OLD_NODE_PATH="$NODE_PATH"
NODE_PATH="$NODE_VIRTUAL_ENV/__MOD_NAME__"
export NODE_PATH

_OLD_NPM_CONFIG_PREFIX="$NPM_CONFIG_PREFIX"
NPM_CONFIG_PREFIX="$NODE_VIRTUAL_ENV"
export NPM_CONFIG_PREFIX

if [ -z "$NODE_VIRTUAL_ENV_DISABLE_PROMPT" ] ; then
    _OLD_NODE_VIRTUAL_PS1="$PS1"
    if [ "x__NODE_VIRTUAL_PROMPT__" != x ] ; then
        PS1="__NODE_VIRTUAL_PROMPT__$PS1"
    else
    if [ "`basename \"$NODE_VIRTUAL_ENV\"`" = "__" ] ; then
        # special case for Aspen magic directories
        # see http://www.zetadev.com/software/aspen/
        PS1="[`basename \`dirname \"$NODE_VIRTUAL_ENV\"\``] $PS1"
    else
        PS1="(`basename \"$NODE_VIRTUAL_ENV\"`)$PS1"
    fi
    fi
    export PS1
fi

# This should detect bash and zsh, which have a hash command that must
# be called to get it to forget past commands.  Without forgetting
# past commands the $PATH changes we made may not be respected
if [ -n "$BASH" -o -n "$ZSH_VERSION" ] ; then
    hash -r
fi
"""

NODE_BAT = """\
@ECHO OFF

SETLOCAL

SET "NODE_VIRTUAL_ENV=__NODE_VIRTUAL_ENV__"
SET "NODE_PATH=%NODE_VIRTUAL_ENV%\\__MOD_NAME__"
SET "NPM_CONFIG_PREFIX=__NODE_VIRTUAL_ENV__\\__BIN_NAME__"

"%NODE_VIRTUAL_ENV%\\__BIN_NAME__\\node-venv.exe" %*

ENDLOCAL
"""

if __name__ == '__main__':
    main()
