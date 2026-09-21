import argparse
import os
import sys


PROJECT = "Argos EPI"

FALLBACK_BANNER = [
    " █████╗ ██████╗  ██████╗  ██████╗ ███████╗    ███████╗██████╗ ██╗",
    "██╔══██╗██╔══██╗██╔════╝ ██╔═══██╗██╔════╝    ██╔════╝██╔══██╗██║",
    "███████║██████╔╝██║  ███╗██║   ██║███████╗    █████╗  ██████╔╝██║",
    "██╔══██║██╔══██╗██║   ██║██║   ██║╚════██║    ██╔══╝  ██╔═══╝ ██║",
    "██║  ██║██║  ██║╚██████╔╝╚██████╔╝███████║    ███████╗██║     ██║",
    "╚═╝  ╚═╝╚═╝  ╚═╝ ╚═════╝  ╚═════╝ ╚══════╝    ╚══════╝╚═╝     ╚═╝",
]

ASCII_FALLBACK = [
    "    _                          _____ ____ ___ ",
    "   / \\   _ __ __ _  ___  ___  | ____|  _ \\_ _|",
    "  / _ \\ | '__/ _` |/ _ \\/ __| |  _| | |_) | | ",
    " / ___ \\| | | (_| | (_) \\__ \\ | |___|  __/| | ",
    "/_/   \\_\\_|  \\__, |\\___/|___/ |_____|_|  |___|",
    "             |___/                            ",
]


def _enable_windows_ansi():
    if os.name != "nt":
        return
    try:
        import ctypes

        kernel32 = ctypes.windll.kernel32
        handle = kernel32.GetStdHandle(-11)
        mode = ctypes.c_uint32()
        if kernel32.GetConsoleMode(handle, ctypes.byref(mode)):
            kernel32.SetConsoleMode(handle, mode.value | 0x0004)
    except Exception:
        pass


def _force_utf8_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


def _pyfiglet_banner():
    try:
        import pyfiglet

        return pyfiglet.figlet_format(PROJECT, font="ansi_shadow").rstrip()
    except Exception:
        return "\n".join(FALLBACK_BANNER)


def _print_text(text):
    try:
        print(text)
    except UnicodeEncodeError:
        print("\n".join(ASCII_FALLBACK))


def print_banner(subtitle=None, compact=False):
    _enable_windows_ansi()
    _force_utf8_stdout()

    green = "\033[92m"
    bold = "\033[1m"
    reset = "\033[0m"
    dim = "\033[2m"

    banner = _pyfiglet_banner()
    _print_text(f"{green}{bold}{banner}{reset}")

    if subtitle:
        width = max(52, min(86, max(len(line) for line in banner.splitlines())))
        line = "=" * min(width, 72)
        print(line)
        print(f" {subtitle}")
        print(line)
    elif not compact:
        print(f"{dim}{PROJECT} - IA para seguranca do trabalho{reset}")
    print()


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--subtitle", default="")
    parser.add_argument("--compact", action="store_true")
    args = parser.parse_args()
    print_banner(args.subtitle, args.compact)


if __name__ == "__main__":
    main()
