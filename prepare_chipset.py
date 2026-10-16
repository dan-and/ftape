#!/usr/bin/env python3
"""Enable the W83627DHG-P floppy controller on the ASRock G41M-S3.

The BIOS leaves the Super I/O floppy function disabled, so the ports at
0x3f0 never respond. This turns on ICH7 floppy decode and logical device 0,
then checks that the status register answers. The programming is lost on
reboot. Run as root, before insert_floppy.sh. Leave the floppy module unloaded.

    ./prepare_chipset.py --check
"""

import argparse
import os
import sys

PCI_CONFIG = "/sys/bus/pci/devices/0000:00:1f.0/config"
LPC_IO_DEC = 0x80
LPC_EN = 0x82
FDD_RANGE_SECONDARY = 1 << 12
FDD_LPC_EN = 1 << 3

SIO_INDEX = 0x2E
SIO_DATA = 0x2F
FDC_BASE = 0x3F0
FDC_MSR = FDC_BASE + 4
FDC_IRQ = 6
FDC_DMA = 2


def outb(fd, port, val):
    os.lseek(fd, port, os.SEEK_SET)
    os.write(fd, bytes([val & 0xFF]))


def inb(fd, port):
    os.lseek(fd, port, os.SEEK_SET)
    return os.read(fd, 1)[0]


def sio_read(fd, reg):
    outb(fd, SIO_INDEX, reg)
    return inb(fd, SIO_DATA)


def sio_write(fd, reg, val):
    outb(fd, SIO_INDEX, reg)
    outb(fd, SIO_DATA, val)


def pci_read16(offset):
    with open(PCI_CONFIG, "rb", buffering=0) as cfg:
        cfg.seek(offset)
        return int.from_bytes(cfg.read(2), "little")


def pci_write16(offset, value):
    with open(PCI_CONFIG, "r+b", buffering=0) as cfg:
        cfg.seek(offset)
        cfg.write((value & 0xFFFF).to_bytes(2, "little"))


def read_ich7():
    dec = pci_read16(LPC_IO_DEC)
    en = pci_read16(LPC_EN)
    return {
        "decode": dec,
        "enable": en,
        "primary": not (dec & FDD_RANGE_SECONDARY),
        "forwarding": bool(en & FDD_LPC_EN),
    }


def enable_ich7_floppy_decode():
    state = read_ich7()
    if not state["primary"]:
        pci_write16(LPC_IO_DEC, state["decode"] & ~FDD_RANGE_SECONDARY)
    if not state["forwarding"]:
        pci_write16(LPC_EN, state["enable"] | FDD_LPC_EN)
    return read_ich7()


def read_w83627_fdc(fd):
    outb(fd, SIO_INDEX, 0x87)
    outb(fd, SIO_INDEX, 0x87)
    try:
        sio_write(fd, 0x07, 0x00)
        return {
            "chip": sio_read(fd, 0x20),
            "rev": sio_read(fd, 0x21),
            "activate": sio_read(fd, 0x30),
            "base": (sio_read(fd, 0x60) << 8) | sio_read(fd, 0x61),
            "irq": sio_read(fd, 0x70),
            "dma": sio_read(fd, 0x74),
        }
    finally:
        outb(fd, SIO_INDEX, 0xAA)


def program_w83627_fdc(fd):
    outb(fd, SIO_INDEX, 0x87)
    outb(fd, SIO_INDEX, 0x87)
    try:
        sio_write(fd, 0x07, 0x00)
        sio_write(fd, 0x60, FDC_BASE >> 8)
        sio_write(fd, 0x61, FDC_BASE & 0xFF)
        sio_write(fd, 0x70, FDC_IRQ)
        sio_write(fd, 0x74, FDC_DMA)
        sio_write(fd, 0x30, 0x01)
    finally:
        outb(fd, SIO_INDEX, 0xAA)


def problems(ich7, sio, msr):
    found = []
    if sio["chip"] not in (0xA0, 0xB0):
        found.append(f"Super I/O id {sio['chip']:02x} is not a W83627DHG")
    if not ich7["forwarding"]:
        found.append("ICH7 floppy decode is off")
    if not ich7["primary"]:
        found.append("ICH7 floppy range is 0x370, not 0x3f0")
    if sio["activate"] != 0x01:
        found.append(f"floppy logical device activate is {sio['activate']:02x}")
    if sio["base"] != FDC_BASE or sio["irq"] != FDC_IRQ or sio["dma"] != FDC_DMA:
        found.append(
            f"floppy logical device is {sio['base']:04x} irq {sio['irq']:02x} dma {sio['dma']:02x}"
        )
    if msr == 0xFF:
        found.append(f"status port 0x{FDC_MSR:03x} reads ff")
    return found


def report(ich7, sio, msr):
    print(f"LPC_IO_DEC {ich7['decode']:04x}  LPC_EN {ich7['enable']:04x}")
    print(f"Super I/O chip {sio['chip']:02x} rev {sio['rev']:02x}")
    print(
        f"activate {sio['activate']:02x} base {sio['base']:04x} "
        f"irq {sio['irq']:02x} dma {sio['dma']:02x}"
    )
    print(f"MSR 0x{FDC_MSR:03x} = {msr:02x}")
    bad = problems(ich7, sio, msr)
    if bad:
        for item in bad:
            print(item)
        print("floppy controller: disabled")
        return 1
    print("floppy controller: enabled")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="report whether the floppy controller is enabled, and change nothing",
    )
    args = parser.parse_args()
    if os.geteuid() != 0:
        sys.exit("run as root")

    if args.check:
        ich7 = read_ich7()
    else:
        ich7 = enable_ich7_floppy_decode()

    fd = os.open("/dev/port", os.O_RDWR)
    try:
        if not args.check:
            program_w83627_fdc(fd)
        sio = read_w83627_fdc(fd)
        msr = inb(fd, FDC_MSR)
    finally:
        os.close(fd)

    sys.exit(report(ich7, sio, msr))


if __name__ == "__main__":
    main()

