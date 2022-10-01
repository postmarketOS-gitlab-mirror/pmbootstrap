# Copyright 2022 Oliver Smith
# SPDX-License-Identifier: GPL-3.0-or-later
import os
import logging
import pmb.chroot
import pmb.helpers.mount


def install_fsprogs(args, filesystem):
    """ Install the package required to format a specific filesystem. """
    fsprogs = pmb.config.filesystems.get(filesystem)
    if not fsprogs:
        raise RuntimeError(f"Unsupported filesystem: {filesystem}")
    pmb.chroot.apk.install(args, [fsprogs])


def format_and_mount_boot(args, device, boot_label):
    """
    :param device: boot partition on install block device (e.g. /dev/installp1)
    :param boot_label: label of the root partition (e.g. "pmOS_boot")

    When adjusting this function, make sure to also adjust
    ondev-prepare-internal-storage.sh in postmarketos-ondev.git!
    """
    mountpoint = "/mnt/install/boot"
    filesystem = args.deviceinfo["boot_filesystem"] or "ext2"
    install_fsprogs(args, filesystem)
    logging.info(f"(native) format {device} (boot, {filesystem}), mount to"
                 " mountpoint")
    if filesystem == "fat16":
        pmb.chroot.root(args, ["mkfs.fat", "-F", "16", "-n", boot_label,
                               device])
    elif filesystem == "fat32":
        pmb.chroot.root(args, ["mkfs.fat", "-F", "32", "-n", boot_label,
                               device])
    elif filesystem == "ext2":
        pmb.chroot.root(args, ["mkfs.ext2", "-F", "-q", "-L", boot_label,
                               device])
    elif filesystem == "btrfs":
        pmb.chroot.root(args, ["mkfs.btrfs", "-f", "-q", "-L", boot_label,
                               device])
    else:
        raise RuntimeError("Filesystem " + filesystem + " is not supported!")
    pmb.chroot.root(args, ["mkdir", "-p", mountpoint])
    pmb.chroot.root(args, ["mount", device, mountpoint])


def format_luks_root(args, device):
    """
    :param device: root partition on install block device (e.g. /dev/installp2)
    """
    mountpoint = "/dev/mapper/pm_crypt"

    logging.info(f"(native) format {device} (root, luks), mount to"
                 f" {mountpoint}")
    logging.info(" *** TYPE IN THE FULL DISK ENCRYPTION PASSWORD (TWICE!) ***")

    # Avoid cryptsetup warning about missing locking directory
    pmb.chroot.root(args, ["mkdir", "-p", "/run/cryptsetup"])

    pmb.chroot.root(args, ["cryptsetup", "luksFormat",
                           "-q",
                           "--cipher", args.cipher,
                           "--iter-time", args.iter_time,
                           "--use-random",
                           device], output="interactive")
    pmb.chroot.root(args, ["cryptsetup", "luksOpen", device, "pm_crypt"],
                    output="interactive")

    if not os.path.exists(f"{args.work}/chroot_native/{mountpoint}"):
        raise RuntimeError("Failed to open cryptdevice!")


def get_root_filesystem(args):
    ret = args.filesystem or args.deviceinfo["root_filesystem"] or "ext4"
    pmaports_cfg = pmb.config.pmaports.read_config(args)

    supported = pmaports_cfg.get("supported_root_filesystems", "ext4")
    supported_list = supported.split(",")

    if ret not in supported_list:
        raise ValueError(f"Root filesystem {ret} is not supported by your"
                         " currently checked out pmaports branch. Update your"
                         " branch ('pmbootstrap pull'), change it"
                         " ('pmbootstrap init'), or select one of these"
                         f" filesystems: {', '.join(supported_list)}")
    return ret

def mount_partition(args, device, mountpoint):
    logging.info("(native) mount " + device + " to " + mountpoint)
    pmb.chroot.root(args, ["mkdir", "-p", mountpoint])
    pmb.chroot.root(args, ["mount", device, mountpoint])

def format_partition(args, device, root_label, sdcard):
    """
    :param device: root partition on install block device (e.g. /dev/installp2)
    :param root_label: label of the root partition (e.g. "pmOS_root")
    :param sdcard: path to sdcard device (e.g. /dev/mmcblk0) or None
    """
    # Format
    if not args.rsync:
        filesystem = get_root_filesystem(args)

        if filesystem == "ext4":
            # Some downstream kernels don't support metadata_csum (#1364).
            # When changing the options of mkfs.ext4, also change them in the
            # recovery zip code (see 'grep -r mkfs\.ext4')!
            mkfs_root_args = ["mkfs.ext4", "-O", "^metadata_csum", "-F",
                              "-q", "-L", root_label]
            # When we don't know the file system size before hand like
            # with non-block devices, we need to explicitely set a number of
            # inodes. See #1717 and #1845 for details
            if not sdcard:
                mkfs_root_args = mkfs_root_args + ["-N", "100000"]
        elif filesystem == "f2fs":
            mkfs_root_args = ["mkfs.f2fs", "-f", "-l", root_label]
        elif filesystem == "btrfs":
            mkfs_root_args = ["mkfs.btrfs", "-f", "-L", root_label]
        else:
            raise RuntimeError(f"Don't know how to format {filesystem}!")

        install_fsprogs(args, filesystem)
        logging.info(f"(native) format {device} (root, {filesystem})")
        pmb.chroot.root(args, mkfs_root_args + [device])

def format_ota(args, partmap, sdcard):
    """
    Format and mount partitions for the OTA partition map, set up overlays etc

    :param partmap: dictionary map of partitions, see pmaports partition_maps dir
    :param sdcard: path to sdcard device (e.g. /dev/mmcblk0) or None
    """

    for i, part in enumerate(partmap.keys):
        dev_path = f"/dev/installp{i}"

        if part == "boot":
            format_and_mount_boot(args, dev_path, partmap[part]["partlabel"])
        elif part == "home" and args.full_disk_encryption:
            format_luks_root(args, dev_path)
            dev_path = "/dev/mapper/pm_crypt"
        format_partition(args, dev_path, partmap[part]["partlabel"], sdcard)
        if not partmap[part]["slot"] or (partmap[part]["slot"] == True and part[-2:] == "_a"):
            mount_partition(args, dev_path, f"/mnt/install/{part[:-2]}")

    dir = "/mnt/install/var/lib/overlays/etc"
    pmb.chroot.root(args, ["mkdir", "-p", f"{dir}/upper"])
    pmb.chroot.root(args, ["mkdir", "-p", f"{dir}/lower"])
    pmb.chroot.root(args, ["mount", "-t", "overlay", "overlay", "-o",
        f"lowerdir=/mnt/install/etc,"
        f"upperdir={dir}/upper,"
        f"workdir={dir}/work"])

    dir = "/mnt/install/home/.pmos/var"
    pmb.helpers.mount.bind(args, f"{dir}/log", "/mnt/install/var/log")
    pmb.helpers.mount.bind(args, f"{dir}/lib/flatpak", "/mnt/install/var/lib/flatpak")
    pmb.helpers.mount.bind(args, f"{dir}/cache/apk", "/mnt/install/var/cache/apk")

def format(args, layout, boot_label, root_label, sdcard):
    """
    :param layout: partition layout from get_partition_layout()
    :param boot_label: label of the boot partition (e.g. "pmOS_boot")
    :param root_label: label of the root partition (e.g. "pmOS_root")
    :param sdcard: path to sdcard device (e.g. /dev/mmcblk0) or None
    """
    root_dev = f"/dev/installp{layout['root']}"
    boot_dev = f"/dev/installp{layout['boot']}"

    if args.full_disk_encryption:
        format_luks_root(args, root_dev)
        root_dev = "/dev/mapper/pm_crypt"

    format_partition(args, root_dev, root_label, sdcard)
    mount_partition(args, root_dev, "/mnt/install")
    format_and_mount_boot(args, boot_dev, boot_label)
