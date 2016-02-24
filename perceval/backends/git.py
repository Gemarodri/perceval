# -*- coding: utf-8 -*-
#
# Copyright (C) 2015-2016 Bitergia
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE. See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program; if not, write to the Free Software
# Foundation, Inc., 59 Temple Place - Suite 330, Boston, MA 02111-1307, USA.
#
# Authors:
#     Santiago Dueñas <sduenas@bitergia.com>
#

import json
import logging
import os
import re
import subprocess

from ..backend import Backend, BackendCommand, metadata
from ..errors import RepositoryError, ParseError
from ..utils import DEFAULT_DATETIME


logger = logging.getLogger(__name__)


def get_update_time(item):
    """Extracts the update time from a Git item"""
    return item['CommitDate']


class Git(Backend):
    """Git backend.

    This class allows the fetch the commits from a Git repository
    (local or remote) or from a log file. To initialize this class,
    you have to provide the URI repository and a value for `gitpath`.

    When `gitpath` is a directory or does not exist, it will be
    considered as the place where the repository is/will be cloned;
    when `gitpath` is a file it will be considered as a Git log file.

    :param uri: URI of the Git repository
    :param gitpath: path to the repository or to the log file
    :param cache: cache object to store raw data

    :raises RepositoryError: raised when there was an error cloning or
        updating the repository.
    """
    version = '0.1.0'

    def __init__(self, uri, gitpath, cache=None):
        super().__init__(uri, cache=cache)
        self.uri = uri
        self.gitpath = gitpath

    @metadata(get_update_time)
    def fetch(self, from_date=DEFAULT_DATETIME):
        """Fetch commits.

        The method retrieves from a Git repository or a log file
        a list of commits since the given date. Commits are returned
        in the same order they were parsed.

        Take into account that `from_date` is ignored when the commits
        are fetched from a Git log file.

        The class raises a `RepositoryError` exception when an error occurs
        accessing the repository.

        :param from_date: obtain commits newer than a specific date
            (inclusive)

        :returns: a generator of commits
        """
        logger.info("Fetching commits: '%s' git repository from %s",
                    self.uri, str(from_date))

        # Ignore default datetime to avoid problems with git
        if from_date == DEFAULT_DATETIME:
            from_date = None

        ncommits = 0
        commits = self.__fetch_and_parse_log(from_date)

        for commit in commits:
            yield commit
            ncommits += 1

        logger.info("Fetch process completed: %s commits fetched",
                    ncommits)

    def __fetch_and_parse_log(self, from_date):
        if os.path.isfile(self.gitpath):
            return self.parse_git_log_from_file(self.gitpath)
        else:
            repo = self.__create_and_update_git_repository()
            gitlog = repo.log(from_date)
            return self.parse_git_log_from_iter(gitlog)

    def __create_and_update_git_repository(self):
        if not os.path.exists(self.gitpath):
            repo = GitRepository.clone(self.uri, self.gitpath)
        elif os.path.isdir(self.gitpath):
            repo = GitRepository(self.uri, self.gitpath)
        repo.pull()

        return repo

    @staticmethod
    def parse_git_log_from_file(filepath):
        """Parse a Git log file.

        The method parses the Git log file and returns an iterator of
        dictionaries. Each one of this, contains a commit.

        :param filepath: path to the log file

        :returns: a generator of parsed commits

        :raises ParseError: raised when the format of the Git log file
            is invalid
        :raises OSError: raised when an error occurs reading the
            given file
        """
        with open(filepath, 'r', errors='surrogateescape') as f:
            parser = GitParser(f)

            for commit in parser.parse():
                yield commit

    @staticmethod
    def parse_git_log_from_iter(iterator):
        """Parse a Git log obtained from an iterator.

        The method parses the Git log fetched from an iterator, where
        each item is a line of the log. It returns and iterator of
        dictionaries. Each dictionary contains a commit.

        :param iterator: iterator of Git log lines

        :raises ParseError: raised when the format of the Git log
            is invalid
        """
        parser = GitParser(iterator)

        for commit in parser.parse():
            yield commit


class GitCommand(BackendCommand):
    """Class to run Git backend from the command line."""

    def __init__(self, *args):
        super().__init__(*args)

        self.uri = self.parsed_args.uri
        self.outfile = self.parsed_args.outfile

        if self.parsed_args.git_log:
            git_path = self.parsed_args.git_log
        elif not self.parsed_args.git_path:
            base_path = os.path.expanduser('~/.perceval/repositories/')
            git_path = os.path.join(base_path, self.uri)
        else:
            git_path = self.parsed_args.git_path

        cache = None

        self.backend = Git(self.uri, git_path, cache=cache)

    def run(self):
        """Fetch and print the commits.

        This method runs the backend to fetch the commits from the given
        git log. Commits are converted to JSON objects and printed to the
        defined output.
        """
        commits = self.backend.fetch()

        try:
            for commit in commits:
                obj = json.dumps(commit, indent=4, sort_keys=True)
                self.outfile.write(obj)
                self.outfile.write('\n')
        except OSError as e:
            raise RuntimeError(str(e))
        except Exception as e:
            raise RuntimeError(str(e))

    @classmethod
    def create_argument_parser(cls):
        """Returns the Git argument parser."""

        parser = super().create_argument_parser()

        # Mutual exclusive parameters
        group = parser.add_mutually_exclusive_group()
        group.add_argument('--git-path', dest='git_path',
                           help="Path where the Git repository will be cloned")
        group.add_argument('--git-log', dest='git_log',
                           help="Path to the Git log file")

        # Required arguments
        parser.add_argument('uri',
                            help="URI of the Git log repository")

        return parser


class GitParser:
    """Git log parser.

    This class parses a plain Git log stream, converting plain commits
    into dict items.

    Not every Git log output is valid to be parsed. The Git log stream
    must have a specific structure. It must contain raw commits data and
    stats about modified files. The next excerpt shows an example of a
    valid log:

        commit aaa7a9209f096aaaadccaaa7089aaaa3f758a703
        Author:     John Smith <jsmith@example.com>
        AuthorDate: Tue Aug 14 14:30:13 2012 -0300
        Commit:     John Smith <jsmith@example.com>
        CommitDate: Tue Aug 14 14:30:13 2012 -0300

            Commit for testing

        :000000 100644 0000000... aaaaaaa... A	aaa/otherthing
        :000000 100644 0000000... aaaaaaa... A	aaa/something
        :000000 100644 0000000... aaaaaaa... A	bbb/bthing
        0	0	aaa/otherthing
        0	0	aaa/something
        0	0	bbb/bthing

    Each commit starts with the 'commit' tag that is followed by the
    SHA-1 of the commit, its parents (two or more parents in the case
    of a merge) and a list of refs, if any.

        commit 456a68ee1407a77f3e804a30dff245bb6c6b872f
               ce8e0b86a1e9877f42fe9453ede418519115f367
               51a3b654f252210572297f47597b31527c475fb8
               (HEAD -> refs/heads/master)

    The commit line is followed by one or more headers. Each header
    has a key and a value:

        Author:     John Smith <jsmith@example.com>
        AuthorDate: Tue Aug 14 14:30:13 2012 -0300
        Commit:     John Smith <jsmith@example.com>
        CommitDate: Tue Aug 14 14:30:13 2012 -0300

    Then, an empty line divides the headers from the commit message.

        First line of the commit

        Commit message splitted into one or several lines.
        Each line of the message stars with 4 spaces.

    After a new empty line, actions and stats over files can be found.
    A action line starts with one or more ':' chars and contain data
    about the old and new permissions of a file, its old and new indexes,
    the action code and the filepath to the file. In the case of a copied,
    renamed or moved file, the new filepath to that file is included.

        :100644 100644 e69de29... e69de29... R100	aaa/otherthing	aaa/otherthing.renamed

    Stats lines include the number of lines added and removed, and the
    name of the file. The new name is also included for moved or renamed
    files.

        10	0	aaa/{otherthing => otherthing.renamed}

    The commit ends with an empty line.

    This example was generated using the next command:

        git log --raw --numstat --pretty=fuller --decorate=full \
                --parents -M -C -c --remotes=origin --all

    :param stream: a file object which stores the log
    """
    COMMIT_PATTERN = r"""^commit[ \t](?P<commit>[a-f0-9]{40})
                     (?:[ \t](?P<parents>[a-f0-9][a-f0-9 \t]+))?
                     (?:[ \t]\((?P<refs>.+)\))?$
                     """

    HEADER_PATTERN = r"^(?P<header>[a-zA-z0-9]+)\:[ \t]+(?P<value>.+)$"

    MESSAGE_LINE_PATTERN = r"^[\s]{4}(?P<msg>.*)$"

    ACTION_PATTERN = r"""^(?P<sc>\:+)
                      (?P<modes>(?:\d{6}[ \t])+)
                      (?P<indexes>(?:[a-f0-9]+\.{,3}[ \t])+)
                      (?P<action>[^\t]+)\t+
                      (?P<file>[^\t]+)
                      (?:\t+(?P<newfile>.+))?$"""

    STATS_PATTERN = r"^(?P<added>\d+|-)[ \t]+(?P<removed>\d+|-)[ \t]+(?P<file>.+)$"

    EMPTY_LINE_PATTERN = r"^$"

    # Compiled patterns
    GIT_COMMIT_REGEXP = re.compile(COMMIT_PATTERN, re.VERBOSE)
    GIT_HEADER_REGEXP = re.compile(HEADER_PATTERN, re.VERBOSE)
    GIT_MESSAGE_REGEXP = re.compile(MESSAGE_LINE_PATTERN, re.VERBOSE)
    GIT_ACTION_REGEXP = re.compile(ACTION_PATTERN, re.VERBOSE)
    GIT_STATS_REGEXP = re.compile(STATS_PATTERN, re.VERBOSE)
    GIT_NEXT_STATE_REGEXP = re.compile(EMPTY_LINE_PATTERN, re.VERBOSE)

    # Git parser status
    (COMMIT,
     HEADER,
     MESSAGE,
     FILE) = range(4)

    def __init__(self, stream):
        self.stream = stream
        self.nline = 0
        self.state = self.COMMIT

        # Aux vars to store the commit that is being parsed
        self.commit = None
        self.commit_files = {}

        self.handlers = {
            self.COMMIT : self._handle_commit,
            self.HEADER : self._handle_header,
            self.MESSAGE : self._handle_message,
            self.FILE : self._handle_file
        }

    def parse(self):
        """Parse the Git log stream."""

        for line in self.stream:
            line = line.rstrip('\n')
            parsed = False
            self.nline += 1

            while not parsed:
                parsed = self.handlers[self.state](line)

                if self.state == self.COMMIT:
                    commit = self._build_commit()
                    logger.debug("Commit %s parsed", commit['commit'])
                    yield commit

        # Return the last commit, if any
        if self.commit:
            commit = self._build_commit()
            logger.debug("Commit %s parsed", commit['commit'])
            yield commit

    def _build_commit(self):
        def remove_none_values(d):
            return {k: v for k, v in d.items() if v != None}

        commit = self.commit
        commit = remove_none_values(commit)
        commit['files'] = [remove_none_values(item) \
                           for _, item in sorted(self.commit_files.items())]

        self.commit = None
        self.commit_files = {}

        return commit

    def _handle_commit(self, line):
        m = self.GIT_COMMIT_REGEXP.match(line)
        if not m:
            msg = "commit expected on line %s" % (str(self.nline))
            raise ParseError(cause=msg)

        parents = self.__parse_data_list(m.group('parents'))
        refs = self.__parse_data_list(m.group('refs'), sep=',')

        # Initialize a new commit
        self.commit = {}
        self.commit['commit'] = m.group('commit')
        self.commit['parents'] = parents
        self.commit['refs'] = refs

        self.state = self.HEADER

        return True

    def _handle_header(self, line):
        m = self.GIT_NEXT_STATE_REGEXP.match(line)
        if m:
            self.state = self.MESSAGE
            return True

        m = self.GIT_HEADER_REGEXP.match(line)
        if not m:
            msg = "invalid header format on line %s" % (str(self.nline))
            raise ParseError(cause=msg)

        header = m.group('header')
        value = m.group('value')
        self.commit[header] = value

        return True

    def _handle_message(self, line):
        m = self.GIT_NEXT_STATE_REGEXP.match(line)
        if m:
            self.state = self.FILE
            return True

        m = self.GIT_MESSAGE_REGEXP.match(line)
        if not m:
            logger.debug("Invalid message format on line %s. Skipping.",
                         str(self.nline))
            self.state = self.FILE
            return False

        # Concatenate message lines
        if not 'message' in self.commit:
            self.commit['message'] = ''
        else:
            self.commit['message'] += '\n'
        self.commit['message'] += m.group('msg')

        return True

    def _handle_file(self, line):
        m = self.GIT_NEXT_STATE_REGEXP.match(line)
        if m:
            self.state = self.COMMIT
            return True

        m = self.GIT_ACTION_REGEXP.match(line)
        if m:
            data = m.groupdict()
            self._handle_action_data(data)
            return True

        m = self.GIT_STATS_REGEXP.match(line)
        if m:
            data = m.groupdict()
            self._handle_stats_data(data)
            return True

        # No match case
        logger.debug("Invalid action format on line %s. Skipping.",
                     str(self.nline))
        self.state = self.COMMIT
        return False

    def _handle_action_data(self, data):
        modes = self.__parse_data_list(data['modes'])
        indexes = self.__parse_data_list(data['indexes'])
        filename = data['file']

        if filename not in self.commit_files:
            self.commit_files[filename] = {}

        self.commit_files[filename]['modes'] = modes
        self.commit_files[filename]['indexes'] = indexes
        self.commit_files[filename]['action'] = data['action']
        self.commit_files[filename]['file'] = filename
        self.commit_files[filename]['newfile'] = data['newfile']

    def _handle_stats_data(self, data):
        filename = self.__get_old_filepath(data['file'])

        if filename not in self.commit_files:
            self.commit_files[filename] = {}

        self.commit_files[filename]['added'] = data['added']
        self.commit_files[filename]['removed'] = data['removed']

    def __parse_data_list(self, data, sep=' '):
        if data:
            l = data.strip().split(sep)
            return [e.strip() for e in l]
        else:
            return []

    def __get_old_filepath(self, f):
        """Get the old filepath of a moved/renamed file.

        Moved or renamed files can be found in the log with the next
        patterns: '{old_prefix => new_prefix}/name' or
        'name/{old_suffix => new_suffix}'. This method returns the
        filepath before the file was moved or renamed.
        """
        i = f.find('{')
        j = f.find('}')

        if i > -1 and j > -1:
            prefix = f[0:i]
            inner = f[i+1:f.find(' => ', i)]
            suffix = f[j+1:]
            return prefix + inner + suffix
        else:
            return f


class GitRepository:
    """Manage a Git repository.

    This class provides access to a Git repository running some
    common commands such as `clone`, `pull` or `log`.
    To create an instance from a remote repository, use `clone()`
    class method.

    :param uri: URI of the repository
    :param dirpath: local directory where the repository is stored
    """
    def __init__(self, uri, dirpath):
        gitdir = os.path.join(dirpath, '.git')

        if not os.path.exists(gitdir):
            cause = "git repository '%s' does not exist" % dirpath
            raise RepositoryError(cause=cause)

        self.uri = uri
        self.dirpath = dirpath

    @classmethod
    def clone(cls, uri, dirpath):
        """Clone a Git repository.

        Clone the repository stored in `uri` into `dirpath`. The repository
        would be either local or remote.

        :param uri: URI of the repository
        :param dirtpath: directory where the repository will be cloned

        :returns: a `GitRepository` class having cloned the repository

        :raises RepositoryError: when an error occurs cloning the given
            repository
        """
        cmd = ['git', 'clone', uri, dirpath]
        cls._exec(cmd, env={'LANG' : 'C'})

        logging.debug("Git %s repository cloned into %s",
                      uri, dirpath)

        return cls(uri, dirpath)

    def pull(self):
        """Update repository from 'origin' remote.

        Calling this method, the repository will be synchronized with
        'origin' repository. Any commit stored in the local copy will
        be removed.

        :raises RepositoryError: when an error occurs updating the
            repository
        """
        cmd_fetch = ['git', 'fetch', 'origin']
        cmd_reset = ['git', 'reset', '--hard', 'origin']

        self._exec(cmd_fetch, cwd=self.dirpath, env={'LANG' : 'C'})
        self._exec(cmd_reset, cwd=self.dirpath, env={'LANG' : 'C'})

        logging.debug("Git %s repository pulled into %s",
                      self.uri, self.dirpath)

    def log(self, from_date=None, encoding='utf-8'):
        """Read the commit log from the repository.

        The method returns the Git log of the repository using the
        following options:

            git log --raw --numstat --pretty=fuller --decorate=full
                --all --reverse --topo-order --parents -M -C -c
                --remotes=origin

        When `from_date` is given, it gets the commits equal or older
        than that date. This date is given in a datetime object.

        :param from_date: fetch commits newer than a specific
            date (inclusive)
        :param encoding: encode the log using this format

        :returns: a generator where each item is a line from the log

        :raises RepositoryError: when an error occurs fetching the log
        """
        cmd_log = ['git', 'log', '--raw', '--numstat', '--pretty=fuller',
                   '--decorate=full', '--all', '--reverse', '--topo-order',
                   '--parents', '-M', '-C', '-c', '--remotes=origin']

        if from_date:
            dt = from_date.strftime("%Y-%m-%d %H:%M:%S")
            cmd_log.append('--since=' + dt)

        gitlog = self._exec(cmd_log, cwd=self.dirpath,
                            env={'LANG' : 'C', 'PAGER' : ''})
        gitlog = gitlog.split(b'\n')

        logging.debug("Git log fetched from %s repository (%s)",
                      self.uri, self.dirpath)

        for line in gitlog:
            line = line.decode(encoding, errors='surrogateescape')
            yield line

    @staticmethod
    def _exec(cmd, cwd=None, env=None):
        """Run a command.

        Execute `cmd` command in the directory set by `cwd`. Enviroment
        variables can be set using the `env` dictionary. The output
        data is returned as encoded bytes.

        :returns: the output of the command as encoded bytes

        :raises RepositoryError: when an error occurs running the command
        """
        logging.debug("Running command %s (cwd: %s, env: %s)",
                      ' '.join(cmd), cwd, str(env))

        try:
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE,
                                    cwd=cwd, env=env)
            (outs, errs) = proc.communicate()
        except OSError as e:
            raise RepositoryError(cause=str(e))

        if proc.returncode != 0:
            err = errs.decode('utf-8', errors='surrogateescape')
            cause = "git command - %s" % err
            raise RepositoryError(cause=cause)
        else:
            logging.debug(errs.decode('utf-8', errors='surrogateescape'))

        return outs
