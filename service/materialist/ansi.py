class AnsiColor(object):
    """256-color mode https://en.wikipedia.org/wiki/ANSI_escape_code#8-bit"""

    reset = "\x1b[0m"

    def __init__(self, n):
        self.fg = f"\x1b[38;5;{n}m"
        # self.bg = f"\x1b[48;5;{n}m"

    def __call__(self, str):
        return f"{self.fg}{str}{self.reset}"


dark_black = AnsiColor(0)
dark_red = AnsiColor(1)
dark_green = AnsiColor(2)
dark_yellow = AnsiColor(3)
dark_blue = AnsiColor(4)
dark_magenta = AnsiColor(5)
dark_teal = AnsiColor(6)
grey = AnsiColor(7)
dark_grey = AnsiColor(8)
red = AnsiColor(9)
green = AnsiColor(10)
yellow = AnsiColor(11)
blue = AnsiColor(12)
magenta = AnsiColor(13)
teal = AnsiColor(14)
white = AnsiColor(15)
