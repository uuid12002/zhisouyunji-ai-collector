"""Shared desktop typography and semantic colors."""

from tkinter import ttk

BG = '#F4F6FA'
INK = '#192333'
MUTED = '#657086'
BLUE = '#3264ED'


def apply_theme(root):
    import tkinter.font as font

    for name in ('TkDefaultFont', 'TkTextFont', 'TkMenuFont', 'TkHeadingFont'):
        font.nametofont(name).configure(family='Microsoft YaHei UI', size=11)
    style = ttk.Style(root)
    style.theme_use('clam')
    root.configure(background=BG)
    style.configure('.', font=('Microsoft YaHei UI', 11), foreground=INK)
    style.configure('TFrame', background='white')
    style.configure('Page.TFrame', background=BG)
    style.configure('TLabel', background='white', foreground=INK)
    style.configure('Page.TLabel', background=BG)
    style.configure('Title.TLabel', background=BG, font=('Microsoft YaHei UI', 23, 'bold'))
    style.configure('Section.TLabel', font=('Microsoft YaHei UI', 13, 'bold'))
    style.configure('Muted.TLabel', foreground=MUTED)
    style.configure(
        'TButton',
        padding=(14, 9),
        background='white',
        bordercolor='#D8DEEA',
        relief='solid',
        borderwidth=1,
    )
    style.map('TButton', background=[('active', '#EDF2FF')], foreground=[('disabled', '#9BA3B2')])
    style.configure(
        'Primary.TButton',
        background=BLUE,
        foreground='white',
        bordercolor=BLUE,
        font=('Microsoft YaHei UI', 11, 'bold'),
    )
    style.map(
        'Primary.TButton',
        background=[('disabled', '#CCD6EE'), ('active', '#244FD0')],
        foreground=[('disabled', '#8A96AE'), ('!disabled', 'white')],
    )
    style.configure('Nav.TButton', padding=(18, 13), anchor='w', borderwidth=0, background=BG)
    style.configure('Selected.Nav.TButton', background='#E1E9FF', foreground=BLUE)
    style.map('Selected.Nav.TButton', background=[('active', '#D5E0FF')])
    style.configure('TCheckbutton', background='white', padding=6)
    style.map('TCheckbutton', background=[('active', '#F5F8FF')])
    style.configure('TEntry', padding=8, fieldbackground='white', bordercolor='#D8DEEA')
    style.configure('TCombobox', padding=7, fieldbackground='white', bordercolor='#D8DEEA')
    style.configure(
        'Treeview', rowheight=34, fieldbackground='white', background='white', borderwidth=0
    )
    style.configure(
        'Treeview.Heading',
        background='#F3F5FA',
        padding=(8, 9),
        font=('Microsoft YaHei UI', 10, 'bold'),
        relief='solid',
        borderwidth=1,
    )
    style.map('Treeview', background=[('selected', '#E4ECFF')], foreground=[('selected', INK)])
    style.configure('TLabelframe', background='white', bordercolor='#E0E5EF')
    style.configure(
        'TLabelframe.Label', background='white', font=('Microsoft YaHei UI', 11, 'bold')
    )
    return style


def card(parent, title=None, **kwargs):
    frame = ttk.Frame(parent, padding=18, **kwargs)
    if title:
        ttk.Label(frame, text=title, style='Section.TLabel').pack(anchor='w', pady=(0, 14))
    return frame
