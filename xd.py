#!/usr/bin/env python

# Author: Jiqing Tang

import cPickle
import os
import shutil
import string
import subprocess
import sys

#------------------------------ constants ------------------------------

XD_DIR_ENV = 'XD_DIR'

XD_DIFF_ENV = 'XD_DIFF'

#------------------------------ utilities ------------------------------

def importStar(name, **additional):
  __import__(name)
  m = sys.modules[name]
  g = globals()
  g.update(additional)
  if hasattr(m, '__all__'):
    for var in m.__all__:
      g[var] = getattr(m, var)
  else:
    for var, value in vars(m).iteritems():
      if not var.startswith('_'):
        g[var] = value

def isWritableDir(path):
  if path and os.path.isdir(path) and os.access(path, os.R_OK | os.W_OK):
    statvfs = os.statvfs(path)
    return statvfs.f_bavail > 0 and statvfs.f_favail > 0
  return False

def isLinkDir(path):
  return path and os.path.islink(path) and os.path.isdir(path)

def isExecutable(path):
  return path and os.path.isfile(path) and os.access(path, os.X_OK)

def which(program):
  if os.path.sep in program:
    if isExecutable(program):
      return program
  else:
    for path in os.environ['PATH'].split(os.pathsep):
      full_path = os.path.join(path, program)
      if isExecutable(full_path):
        return full_path
  return None

def appendCmdline(cmdline, data):
  i = 1
  while i < len(cmdline) and cmdline[i] != '--':
    i += 1
  if not isinstance(data, (tuple, list)):
    data = [data]
  cmdline[i:i] = data

def escapeShell(s):
  for c in s:
    if not (c.isalnum() or c in r'@%^-_=+:,./'):
      break
  else:
    return s
  if "'" in s:
    return '"%s"' % ''.join(c in '$`"\\' and '\\' + c or c for c in s)
  else:
    return "'%s'" % s

def abbrPath(path):

  def check(real, short):
    if real == path or path.startswith(real + '/'):
      expanded = short + path[len(real):]
      if len(expanded) < len(shortest[0]):
        shortest[0] = expanded

  shortest = [path]
  path = os.path.realpath(path)
  check(path, path)
  home = os.path.realpath(os.path.expanduser('~'))
  check(home, '~')
  for s in os.listdir(home):
    if not s.startswith('.') and isLinkDir(os.path.join(home, s)):
      check(os.path.realpath(os.path.join(home, s)),
            os.path.join('~', s))
  return shortest[0]

def isText(lines):
  null_trans = string.maketrans('', '')
  for line in lines:
    if line.translate(null_trans, string.printable):
      return False
  return True

#------------------------------ scm ------------------------------

class ScmMeta(type):

  all = []

  def __init__(self, name, bases, body):
    super(ScmMeta, self).__init__(name, bases, body)
    ScmMeta.all.append(self)

  @classmethod
  def get(cls, path='.'):
    for scm in cls.all:
      if hasattr(scm, 'detect') and callable(scm.detect) and scm.detect(path):
        return scm
    return None

  @classmethod
  def getByName(cls, name):
    name = name.lower()
    for scm in cls.all:
      if scm.__name__.lower() == name:
        return scm
    return None


class Scm(object):

  __metaclass__ = ScmMeta

  tmpdir = None

  @classmethod
  def getTmpDir(cls):
    if cls.tmpdir is None:
      cls.tmpdir = os.path.abspath(cls.findTmpDir())
    return cls.tmpdir

  @classmethod
  def isTmpFile(cls, path):
    dirname = os.path.dirname(os.path.abspath(path))
    tmpdir = cls.getTmpDir()
    return dirname.startswith(tmpdir)

  @classmethod
  def setupCmdLine(cls, argv, diff_commands=('diff',)):
    cmdline = [cls.__name__.lower()]
    i = 1
    while i < len(argv) and argv[i] != '--':
      if argv[i] in diff_commands:
        break
      i += 1
    else:
      cmdline.append('diff')
    cmdline.extend(argv[1:])
    return cmdline

  @classmethod
  def save(cls, parsed, xd_dir, prefix=''):
    for i in (1, 2):
      local_path = parsed['local_path%d' % i]
      xd_dir_path = cls.getUniqueName(parsed, i).replace('/', '_')
      xd_dir_path = os.path.join(
          xd_dir, '%sf%s__%s' % (prefix, i, xd_dir_path))
      parsed['xd_dir_path%d' % i] = xd_dir_path
      if cls.isTmpFile(local_path):
        if os.stat(local_path).st_dev == os.stat(xd_dir).st_dev:
          os.link(local_path, xd_dir_path)
        else:
          shutil.copy(local_path, xd_dir_path)
        parsed['f%s' % i] = escapeShell(xd_dir_path)
      else:
        os.symlink(os.path.abspath(local_path), xd_dir_path)
        parsed['f%s' % i] = escapeShell(os.path.abspath(local_path))
      parsed['l%s' % i] = escapeShell(parsed['label%s' % i])

#------------------------------ scm svn ------------------------------

class Svn(Scm):

  @staticmethod
  def detect(path='.'):
    return os.path.isdir(os.path.join(path, '.svn'))

  @staticmethod
  def parseArgs(argv=sys.argv):
    parsed = {}
    parsed['local_path1'], parsed['local_path2'] = argv[-2:]
    parsed['label1'], parsed['label2'] = argv[-5], argv[-3]
    parsed['flags'] = argv[1:-6]
    parsed['path1'], parsed['revision1'] = parsed['label1'].rsplit('\t', 1)
    parsed['path2'], parsed['revision2'] = parsed['label2'].rsplit('\t', 1)
    if parsed['path1'] == parsed['path2']:
      parsed['path'] = parsed['path1']
    else:
      parsed['path'] = '%s (VS) %s' % (parsed['path1'], parsed['path2'])
    for i in (1, 2):
      rev = parsed['revision%s' % i]
      if rev.startswith('(') and rev.endswith(')'):
        rev = rev[1:-1]
      if rev.startswith('revision '):
        rev = rev[9:]
      parsed['rev%s' % i] = rev
    return parsed

  @staticmethod
  def findTmpDir():
    for env in ('TMP', 'TEMP', 'TMPDIR'):
      dirname = os.environ.get(env)
      if isWritableDir(dirname):
        return dirname

    for dirname in ('/tmp', '/usr/tmp', '/var/tmp'):
      if isWritableDir(dirname):
        return dirname

    return '.'

  @classmethod
  def isTmpFile(cls, path):
    return (super(Svn, cls).isTmpFile(path) or
            os.path.dirname(os.path.abspath(path)).endswith('/.svn/tmp'))

  @staticmethod
  def getUniqueName(parsed, i):
    revision = filter(str.isdigit, parsed['revision%s' % i])
    return '%s__%s' % (parsed['path%s' % i],
                       revision and 'r' + revision or 'WC')

  @staticmethod
  def setupExternalDiff(cmdline, env, xd=sys.argv[0]):
    appendCmdline(cmdline, ['--diff-cmd', xd])

#------------------------------ scm git ------------------------------

class Git(Scm):

  @staticmethod
  def detect(path='.'):
    path = os.path.abspath(path)
    while path != '/':
      if os.path.isdir(os.path.join(path, '.git')):
        return True
      path = os.path.abspath(os.path.join(path, '..'))
    return False

  @staticmethod
  def abbrHashLen(parsed, min_len=7):
    for l in xrange(min_len, 41):
      if parsed['hash1'][:l] != parsed['hash2'][:l]:
        return l

  @classmethod
  def parseArgs(cls, argv=sys.argv):
    parsed = {}
    parsed['path'] = argv[-7]
    parsed['local_path1'], parsed['hash1'], parsed['mode1'] = argv[-6:-3]
    parsed['local_path2'], parsed['hash2'], parsed['mode2'] = argv[-3:]
    parsed['flags'] = argv[1:-7]
    parsed['path1'] = parsed['path2'] = parsed['path']
    l = cls.abbrHashLen(parsed)
    for i in (1, 2):
      hash = parsed['hash%s' % i]
      if hash == '.':
        display_hash = 'no hash'
      elif hash == '0' * 40:
        display_hash = 'working copy'
      else:
        display_hash = 'hash %s' % hash[:l]
        if l < 40:
          display_hash += '...'
      parsed['label%s' % i] = '%s\t(%s)' % (parsed['path'], display_hash)
    return parsed

  @staticmethod
  def findTmpDir():
    return os.environ.get('TMPDIR', '/tmp')

  @classmethod
  def getUniqueName(cls, parsed, i):
    hash = parsed['hash%s' % i]
    if hash == '.':
      display_hash = 'X'
    elif hash == '0' * 40:
      display_hash = 'WC'
    else:
      display_hash = 'h%s' % hash[:cls.abbrHashLen(parsed)]
    return '%s__%s' % (parsed['path'], display_hash)

  @classmethod
  def setupCmdLine(cls, argv):
    return super(Git, cls).setupCmdLine(argv, ('diff', 'show'))

  @staticmethod
  def setupExternalDiff(cmdline, env, xd=sys.argv[0]):
    appendCmdline(cmdline, '--ext-diff')
    env['GIT_EXTERNAL_DIFF'] = xd

#------------------------------ diff tools ------------------------------

def initDiffTool():

  import shlex

  class DiffTool(object):

    all = []

    def __init__(self, command, name=None):
      self.command = command
      self.commands = shlex.split(command)
      self.name = name or self.commands[0]
      self.installed = None
      DiffTool.all.append(self)

    def isInstalled(self, refresh=False):
      if refresh or self.installed is None:
        self.installed = which(self.commands[0]) is not None
      return self.installed

    def launch(self, parsed):
      return self.launchCustom(self.command, parsed)

    @staticmethod
    def launchCustom(command, parsed):
      return subprocess.Popen(
          string.Template(command).safe_substitute(parsed),
          close_fds=True,
          shell=True)

  DiffTool('tkdiff -L $l1 -L $l2 -- $f1 $f2')
  DiffTool('xxdiff --title1 $l1 --title2 $l2 -- $f1 $f2')
  DiffTool('gvimdiff -- $f1 $f2')
  DiffTool('emacs --eval \'(ediff "$f1" "$f2")\'', 'emacs(ediff)')
  DiffTool('xemacs --eval \'(ediff "$f1" "$f2")\'', 'xemacs(ediff)')
  DiffTool('meld -L $l1 -L $l2 -- $f1 $f2')
  DiffTool('diffuse $f1 $f2')
  DiffTool('kompare -- $f1 $f2')
  DiffTool('kdiff3 -L1 $l1 -L2 $l2 -- $f1 $f2')

  return DiffTool

#------------------------------ gui ------------------------------

def startGui(scm, xd_dir, cmdline, env, display_cmdline):

  import difflib
  importStar('Tkinter', READONLY='readonly')
  importStar('tkFont')

  DiffTool = initDiffTool()

  class App(Tk):

    def __init__(self):
      Tk.__init__(self)
      self.title('xd: ' + abbrPath(os.getcwd()))
      self.initFonts()
      self.initWidgets()
      self.initContents()

    def initFonts(self):
      size = Label(self)['font'].split()[1]
      self.command_font = Font(family='courier', size=size, weight=BOLD)
      self.fixed_font = Font(family='courier', size=size)
      self.fixed_bold_font = Font(family='courier', size=size, weight=BOLD)

    def initWidgets(self):

      def initCommandFrame(parent):
        f = Frame(parent)
        l = Label(f, text='Command: ')
        e = Entry(f, font=self.command_font, state=READONLY,
                  textvariable=StringVar(value=display_cmdline))
        b = Button(f, text='Rerun', command=self.reRunCommand)

        l.grid(row=0, column=0)
        e.grid(row=0, column=1, sticky=EW)
        b.grid(row=0, column=2)
        f.columnconfigure(1, weight=1)

        return f

      def initPanedWindow(parent):

        def initFileListFrame(parent):
          lf = LabelFrame(parent, labelanchor=N, text='0 pairs of files')
          l = Listbox(lf, selectmode=SINGLE, bg='white', exportselection=0,
                      font=self.fixed_bold_font)
          s = Scrollbar(lf, orient=VERTICAL, takefocus=False, command=l.yview)
          l.config(yscrollcommand=s.set)
          l.bind('<Button-1>', lambda _: l.focus())
          l.bind('<Return>',
                 lambda _: (l.select_clear(0, END),
                            l.select_set(ACTIVE),
                            l.event_generate('<<ListboxSelect>>'),
                            self.launchDiffTool(l.index(ACTIVE))))
          l.bind('<<ListboxSelect>>', self.previewDiff)
          l.bind('<Double-Button-1>',
                 lambda e: self.launchDiffTool(l.nearest(e.y)))

          l.grid(row=0, column=0, sticky=EW + NS)
          s.grid(row=0, column=1, sticky=NS)
          lf.columnconfigure(0, weight=1)
          lf.rowconfigure(0, weight=1)

          self.file_listbox_labelframe = lf
          self.file_listbox = l
          return lf

        def initPreviewFrame(parent):
          f = Frame(parent)
          t = Text(f, name='text', width=81, bg='white', font=self.fixed_font)
          t.tag_config('meta', background='darkgrey', font=self.fixed_bold_font)
          t.tag_config('hunk', foreground='blue')
          t.tag_config('add', foreground='forestgreen')
          t.tag_config('del', foreground='red')
          s = Scrollbar(f, orient=VERTICAL, takefocus=False, command=t.yview)
          t.config(yscrollcommand=s.set)
          t.bind('<Key>', lambda e: e.char and 'break')
          t.bind('<Return>', lambda e: self.launchDiffTool(e) or 'break')

          t.grid(row=0, column=0, sticky=EW + NS)
          s.grid(row=0, column=1, sticky=NS)
          f.columnconfigure(0, weight=1)
          f.rowconfigure(0, weight=1)

          self.preview_text = t
          return f

        pw = PanedWindow(parent, orient=VERTICAL, showhandle=True)
        pw.add(initFileListFrame(pw))
        pw.add(initPreviewFrame(pw))
        return pw

      def initDiffToolFrame(parent):

        def initCustomFrame(parent):
          f = Frame(parent)
          r = Radiobutton(f, variable=iv, value=len(rs))
          sv = StringVar()
          e = Entry(f, font=self.command_font, bg='white', state=READONLY,
                    textvariable=sv)
          e.bind('<FocusIn>',
                 lambda _: iv.get() != len(rs) and iv.set(len(rs)))
          e.bind('<Return>', self.launchDiffTool)

          r.grid(row=0, column=0)
          e.grid(row=0, column=1, sticky=EW)
          f.columnconfigure(1, weight=1)

          self.custom_diff_stringvar = sv
          self.custom_diff_entry = e
          return f

        lf = LabelFrame(parent, labelanchor=NW,
                        text='Diff Tool (double click to launch)')
        iv = IntVar()
        rs = [Radiobutton(lf, text=tool.name, variable=iv, value=i,
                          state=tool.isInstalled() and NORMAL or DISABLED)
              for i, tool in enumerate(DiffTool.all)]
        cf = initCustomFrame(lf)
        self.bind_class('Radiobutton', '<Button-1>',
                        lambda e: e.widget.focus(),
                        add=True)
        self.bind_class('Radiobutton', '<Double-Button-1>',
                        self.launchDiffTool,
                        add=True)
        self.bind_class('Radiobutton', '<Return>',
                        self.launchDiffTool,
                        add=True)
        iv.trace('w', self.selectDiffTool)

        C = 5
        for i, r in enumerate(rs):
          r.grid(row=i / C, column=i % C, sticky=W)
        rows = (len(rs) - 1) / C + 1
        columns = min(len(rs), C)
        cf.grid(row=rows, column=0, columnspan=columns, sticky=EW)
        for i in xrange(columns):
          lf.columnconfigure(i, weight=1)

        self.diff_intvar = iv

        d = None
        xd_diff = os.environ.get(XD_DIFF_ENV)
        if xd_diff:
          for i, tool in enumerate(DiffTool.all):
            if tool.name == xd_diff or tool.commands[0] == xd_diff:
              if tool.isInstalled():
                d = i
              break
          else:
            d = len(DiffTool.all)
            self.custom_diff_stringvar.set(xd_diff)
        if d is None:
          for i, tool in enumerate(DiffTool.all):
            if tool.isInstalled():
              d = i
              break
          else:
            d = len(DiffTool.all)
        iv.set(d)

        return lf

      cf = initCommandFrame(self)
      pw = initPanedWindow(self)
      dtf = initDiffToolFrame(self)

      cf.grid(row=0, column=0, sticky=EW)
      pw.grid(row=1, column=0, sticky=EW + NS)
      dtf.grid(row=2, column=0, sticky=EW, padx=2, pady=2)
      self.columnconfigure(0, weight=1)
      self.rowconfigure(1, weight=1)

    def initContents(self):
      files_path = os.path.join(xd_dir, 'FILES')
      if os.path.isfile(files_path):
        self.files = cPickle.load(open(files_path))
      else:
        self.files = []
      self.file_listbox_labelframe.config(text='%s pair%s of files' % (
          len(self.files), len(self.files) != 1 and 's' or ''))
      self.file_listbox.insert(END, 'STDOUT')
      self.file_listbox.itemconfig(0, fg='blue', selectforeground='blue')
      for pair in self.files:
        self.file_listbox.insert(END, pair['path'])
      self.file_listbox.select_set(0)
      self.previewDiff(0)
      self.file_listbox.focus()

    def reRunCommand(self):
      shutil.rmtree(xd_dir)
      os.mkdir(xd_dir)
      returncode = runScmDiff(cmdline, env, xd_dir)
      if returncode:
        sys.exit(returncode)
      self.file_listbox.delete(0, END)
      self.initContents()

    def getFileIndex(self, event_or_index):
      if isinstance(event_or_index, (int, long)):
        index = event_or_index
      else:
        selected = self.file_listbox.curselection()
        if selected:
          index = int(selected[0])
        else:
          index = None
      return index

    def previewDiff(self, event_or_index):
      selected = self.getFileIndex(event_or_index)
      if selected is None:
        return

      self.preview_text.delete(1.0, END)
      if selected == 0:
        stdout = os.path.join(xd_dir, 'STDOUT')
        self.preview_text.insert(END, 'PATH: %s\n' % stdout, 'meta')
        self.preview_text.insert(END, open(stdout).read())
      else:
        parsed = self.files[selected - 1]
        for key in ('path', 'rev', 'mode', 'hash'):
          key1 = key + '1'
          key2 = key + '2'
          if key1 in parsed and key2 in parsed:
            if parsed[key1] == parsed[key2]:
              self.preview_text.insert(
                  END, '%s: %s\n' % (key.upper(), parsed[key1]), 'meta')
            else:
              self.preview_text.insert(
                  END,
                  '%s: %s\n%s: %s\n' % (key1.upper(), parsed[key1],
                                        key2.upper(), parsed[key2]),
                  'meta')

        size1 = os.path.getsize(parsed['xd_dir_path1'])
        size2 = os.path.getsize(parsed['xd_dir_path2'])
        MAX_SIZE = 4 * 1024 * 1024
        if size1 > MAX_SIZE or size2 > MAX_SIZE:
          results = [' Files are too big (>%d) to diff\n' % MAX_SIZE,
                     '-file size 1: %d\n' % size1,
                     '+file size 2: %d\n' % size2]
        else:
          lines1 = open(parsed['xd_dir_path1']).readlines()
          lines2 = open(parsed['xd_dir_path2']).readlines()
          max_len1 = lines1 and max(len(line) for line in lines1) or 0
          max_len2 = lines2 and max(len(line) for line in lines2) or 0
          MAX_LINE_LEN = 1024
          if max_len1 > MAX_LINE_LEN or max_len2 > MAX_LINE_LEN:
            results = [' Lines are too long (>%d) to diff\n' % MAX_LINE_LEN,
                       '-max line length 1: %d\n' % max_len1,
                       '+max line length 2: %d\n' % max_len2]
          elif not isText(lines1) or not isText(lines2):
            results = [' Binary files diff\n']
          else:
            results = (line
                       for i, line in enumerate(difflib.unified_diff(
                          lines1, lines2))
                       if i >= 2)

        tags = {'-': 'del', '+': 'add', '@': 'hunk'}
        for i, line in enumerate(results):
          tag = tags.get(line[0])
          self.preview_text.insert(END, line, tag)

    def launchDiffTool(self, event_or_index):
      selected = self.getFileIndex(event_or_index)
      if selected is not None and selected > 0:
        DiffTool.launchCustom(self.custom_diff_stringvar.get(),
                              self.files[selected - 1])

    def selectDiffTool(self, *_):
      iv = self.diff_intvar.get()
      if iv == len(DiffTool.all):
        self.custom_diff_entry.config(state=NORMAL)
        self.custom_diff_entry.focus()
      else:
        self.custom_diff_entry.config(state=READONLY)
        self.custom_diff_stringvar.set(DiffTool.all[iv].command)

  App().mainloop()

#------------------------------ controller ------------------------------

def runScmDiff(cmdline, env, xd_dir):
  p = subprocess.Popen(args=cmdline,
                       env=env,
                       stdin=open(os.devnull),
                       stdout=open(os.path.join(xd_dir, 'STDOUT'), 'w'),
                       close_fds=True)
  p.wait()
  return p.returncode


def mainController(argv=sys.argv):
  scm = ScmMeta.get()
  if scm is None:
    print >>sys.stderr, "fatal: '.' is not managed by scm"
    return 1
  xd_dir = os.path.join(scm.getTmpDir(),
                        'xd.%s.%s' % (scm.__name__.lower(), os.getpid()))
  os.mkdir(xd_dir)
  env = dict(os.environ)
  env[XD_DIR_ENV] = xd_dir
  cmdline = scm.setupCmdLine(argv)
  display_cmdline = ' '.join(escapeShell(cmd) for cmd in cmdline)
  print display_cmdline
  scm.setupExternalDiff(cmdline, env, argv[0])
  print >>open(os.path.join(xd_dir, 'CMDLINE'), 'w'), cmdline
  returncode = runScmDiff(cmdline, env, xd_dir)
  try:
    if returncode != 0:
      return returncode
    if not os.path.isfile(os.path.join(xd_dir, 'FILES')):
      shutil.copyfileobj(open(os.path.join(xd_dir, 'STDOUT')), sys.stdout)
      return 0
    return startGui(scm, xd_dir, cmdline, env, display_cmdline)
  finally:
    shutil.rmtree(xd_dir)

#------------------------------ external diff ------------------------------

def mainExternalDiff(argv=sys.argv, xd_dir=os.environ.get(XD_DIR_ENV, '.')):
  xd_dir = os.path.abspath(xd_dir)
  print >>open(os.path.join(xd_dir, 'ARGS'), 'a'), argv
  files_path = os.path.join(xd_dir, 'FILES')
  if os.path.isfile(files_path):
    files = cPickle.load(open(files_path))
  else:
    files = []
  _, scm_name, _ = os.path.basename(xd_dir).split('.')
  scm = ScmMeta.getByName(scm_name)
  parsed = scm.parseArgs(argv)
  scm.save(parsed, xd_dir, prefix='p%s' % (len(files) + 1))
  files.append(parsed)
  cPickle.dump(files, open(files_path, 'w'))
  return 0

#------------------------------ main ------------------------------

def main(argv=sys.argv):
  if isWritableDir(os.environ.get(XD_DIR_ENV)):
    return mainExternalDiff(argv)
  else:
    return mainController(argv)


if __name__ == '__main__':
  sys.exit(main())
