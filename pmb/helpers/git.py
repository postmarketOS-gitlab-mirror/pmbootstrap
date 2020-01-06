"""
Copyright 2020 Oliver Smith

This file is part of pmbootstrap.

pmbootstrap is free software: you can redistribute it and/or modify
it under the terms of the GNU General Public License as published by
the Free Software Foundation, either version 3 of the License, or
(at your option) any later version.

pmbootstrap is distributed in the hope that it will be useful,
but WITHOUT ANY WARRANTY; without even the implied warranty of
MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
GNU General Public License for more details.

You should have received a copy of the GNU General Public License
along with pmbootstrap.  If not, see <http://www.gnu.org/licenses/>.
"""
import logging
import os

import pmb.build
import pmb.chroot.apk
import pmb.config
import pmb.helpers.run


def get_path(args, name_repo):
    """ Get the path to the repository, which is either the default one in the
        work dir, or a user-specified one in args.

        :returns: full path to repository """
    if name_repo == "pmaports":
        return args.aports
    return args.work + "/cache_git/" + name_repo


def clone(args, name_repo, shallow=True):
    """ Clone a git repository to $WORK/cache_git/$name_repo (or to the
        overridden path set in args, as with pmbootstrap --aports).

        :param name_repo: short alias used for the repository name, from
                          pmb.config.git_repos (e.g. "aports_upstream",
                          "pmaports")
        :param shallow: only clone the last revision of the repository, instead
                        of the entire repository (faster, saves bandwith) """
    # Check for repo name in the config
    if name_repo not in pmb.config.git_repos:
        raise ValueError("No git repository configured for " + name_repo)

    # Skip if already checked out
    path = get_path(args, name_repo)
    if os.path.exists(path):
        return

    # Build git command
    url = pmb.config.git_repos[name_repo]
    command = ["git", "clone"]
    if shallow:
        command += ["--depth=1"]
    command += [url, path]

    # Create parent dir and clone
    logging.info("Clone git repository: " + url)
    os.makedirs(args.work + "/cache_git", exist_ok=True)
    pmb.helpers.run.user(args, command, output="stdout")


def rev_parse(args, path, revision="HEAD", extra_args: list = []):
    """ Run "git rev-parse" in a specific repository dir.

        :param path: to the git repository
        :param extra_args: additional arguments for "git rev-parse". Pass
                           "--abbrev-ref" to get the branch instead of the
                           commit, if possible.
        :returns: commit string like "90cd0ad84d390897efdcf881c0315747a4f3a966"
                  or (with --abbrev-ref): the branch name, e.g. "master" """
    command = ["git", "rev-parse"] + extra_args + [revision]
    rev = pmb.helpers.run.user(args, command, path, output_return=True)
    return rev.rstrip()


def can_fast_forward(args, path, branch_upstream, branch="HEAD"):
    command = ["git", "merge-base", "--is-ancestor", branch, branch_upstream]
    ret = pmb.helpers.run.user(args, command, path, check=False)
    if ret == 0:
        return True
    elif ret == 1:
        return False
    else:
        raise RuntimeError("Unexpected exit code from git: " + str(ret))


def clean_worktree(args, path):
    """ Check if there are not any modified files in the git dir. """
    command = ["git", "status", "--porcelain"]
    return pmb.helpers.run.user(args, command, path, output_return=True) == ""


def get_upstream_remote(args, name_repo):
    """ Find the remote, which matches the git URL from the config. Usually
        "origin", but the user may have set up their git repository
        differently. """
    url = pmb.config.git_repos[name_repo]
    path = get_path(args, name_repo)
    command = ["git", "remote", "-v"]
    output = pmb.helpers.run.user(args, command, path, output_return=True)
    for line in output.split("\n"):
        if url in line:
            return line.split("\t", 1)[0]
    raise RuntimeError("{}: could not find remote name for URL '{}' in git"
                       " repository: {}".format(name_repo, url, path))


def pull(args, name_repo):
    """ Check if on official branch and essentially try 'git pull --ff-only'.
        Instead of really doing 'git pull --ff-only', do it in multiple steps
        (fetch, merge --ff-only), so we can display useful messages depending
        on which part fails.

        :returns: integer, >= 0 on success, < 0 on error """
    branches_official = ["master"]

    # Skip if repo wasn't cloned
    path = get_path(args, name_repo)
    if not os.path.exists(path):
        logging.debug(name_repo + ": repo was not cloned, skipping pull!")
        return 1

    # Skip if not on official branch
    branch = rev_parse(args, path, extra_args=["--abbrev-ref"])
    msg_start = "{} (branch: {}):".format(name_repo, branch)
    if branch not in branches_official:
        logging.warning("{} not on one of the official branches ({}), skipping"
                        " pull!"
                        "".format(msg_start, ", ".join(branches_official)))
        return -1

    # Skip if workdir is not clean
    if not clean_worktree(args, path):
        logging.warning(msg_start + " workdir is not clean, skipping pull!")
        return -2

    # Skip if branch is tracking different remote
    branch_upstream = get_upstream_remote(args, name_repo) + "/" + branch
    remote_ref = rev_parse(args, path, branch + "@{u}", ["--abbrev-ref"])
    if remote_ref != branch_upstream:
        logging.warning("{} is tracking unexpected remote branch '{}' instead"
                        " of '{}'".format(msg_start, remote_ref,
                                          branch_upstream))
        return -3

    # Fetch (exception on failure, meaning connection to server broke)
    logging.info(msg_start + " git pull --ff-only")
    if not args.offline:
        pmb.helpers.run.user(args, ["git", "fetch"], path)

    # Skip if already up to date
    if rev_parse(args, path, branch) == rev_parse(args, path, branch_upstream):
        logging.info(msg_start + " already up to date")
        return 2

    # Skip if we can't fast-forward
    if not can_fast_forward(args, path, branch_upstream):
        logging.warning("{} can't fast-forward to {}, looks like you changed"
                        " the git history of your local branch. Skipping pull!"
                        "".format(msg_start, branch_upstream))
        return -4

    # Fast-forward now (should not fail due to checks above, so it's fine to
    # throw an exception on error)
    command = ["git", "merge", "--ff-only", branch_upstream]
    pmb.helpers.run.user(args, command, path, "stdout")
    return 0
