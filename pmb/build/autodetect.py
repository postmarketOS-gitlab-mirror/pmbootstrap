# Copyright 2021 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import logging
import os

import pmb.config
import pmb.chroot.apk
import pmb.helpers.pmaports
import pmb.parse.arch


def arch_from_deviceinfo(args, pkgname, aport):
    """
    The device- packages are noarch packages. But it only makes sense to build
    them for the device's architecture, which is specified in the deviceinfo
    file.

    :returns: None (no deviceinfo file)
              arch from the deviceinfo (e.g. "armhf")
    """
    # Require a deviceinfo file in the aport
    if not pkgname.startswith("device-"):
        return
    deviceinfo = aport + "/deviceinfo"
    if not os.path.exists(deviceinfo):
        return

    # Return its arch
    device = pkgname.split("-", 1)[1]
    arch = pmb.parse.deviceinfo(args, device)["arch"]
    logging.verbose(pkgname + ": arch from deviceinfo: " + arch)
    return arch


def arch(args, pkgname):
    """
    Find a good default in case the user did not specify for which architecture
    a package should be built.

    :returns: arch string like "x86_64" or "armhf". Preferred order, depending
              on what is supported by the APKBUILD:
              * native arch
              * device arch
              * first arch in the APKBUILD
    """
    aport = pmb.helpers.pmaports.find(args, pkgname)
    ret = arch_from_deviceinfo(args, pkgname, aport)
    if ret:
        return ret

    apkbuild = pmb.parse.apkbuild(args, aport + "/APKBUILD")
    arches = apkbuild["arch"]
    if "noarch" in arches or "all" in arches or args.arch_native in arches:
        return args.arch_native

    arch_device = args.deviceinfo["arch"]
    if arch_device in arches:
        return arch_device

    try:
        return apkbuild["arch"][0]
    except IndexError:
        return None


def suffix(args, apkbuild, arch):
    if arch == args.arch_native:
        return "native"

    if "pmb:cross-native" in apkbuild["options"]:
        return "native"

    return "buildroot_" + arch


def crosscompile(args, apkbuild, arch, suffix):
    """
        :returns: None, "native", "crossdirect" or "distcc"
    """
    if not args.cross:
        return None
    if not pmb.parse.arch.cpu_emulation_required(args, arch):
        return None
    if suffix == "native":
        return "native"
    if args.no_crossdirect or "!pmb:crossdirect" in apkbuild["options"]:
        return "distcc"
    return "crossdirect"
