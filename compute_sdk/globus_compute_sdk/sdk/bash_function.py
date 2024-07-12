import tempfile
import typing as t


class BashResult:

    def __init__(
        self,
        cmd: str,
        stdout: str,
        stderr: str,
        returncode: int,
        exception_name: t.Optional[str] = None,
    ):
        """

        Parameters
        ----------
        cmd: str
            formatted command line string that was executed on the endpoint

        stdout: str
            multiline (default 1K) snippet of stdout

        stderr: str
            multiline (default 1K) snippet of stderr

        returncode: int
            Return code from command execution

        exception_name
        """
        self.cmd = cmd
        self.stdout = stdout
        self.stderr = stderr
        self.returncode = returncode
        self.exception_name = exception_name

    def __str__(self):
        return f"Command {self.cmd} returned with exit status: {self.returncode}"


class BashFunction:

    def __init__(
        self,
        cmd: str,
        stdout: t.Optional[str] = None,
        stderr: t.Optional[str] = None,
        walltime: t.Optional[float] = None,
        # This could be a os.Pathlike, but check windows->unix transition
        rundir: t.Optional[str] = None,
        run_in_sandbox: bool = True,
        snippet_lines=1000,
    ):
        """Initialize a BashFunction

        Parameters
        ----------
        cmd: str
             formattable command line to execute. For e.g:
             "lammps -in {input_file}" where {input_file} is formatted
             with kwargs passed at call time.

        stdout: str | None
            file path to which stdout should be captured

        stderr: str | None
            file path to which stderr should be captures

        walltime: float | None
            duration in seconds after which the command should be interrupted

        rundir: str | None
            directory within which the command should be executed

        run_in_sandbox: bool
            when set, the command will execute within a directory matching
            the task UUID
            default=True

        snippet_lines: int
            Number of lines of stdout/err to capture,
            default=1000

        """
        self.cmd = cmd
        self.stdout = stdout
        self.stderr = stderr
        self.walltime = walltime
        self.rundir = rundir
        self.run_in_sandbox = run_in_sandbox
        self.snippet_lines = snippet_lines

    @property
    def __name__(self):
        # This is required for function registration
        return self.cmd

    def open_std_fd(self, fname, mode: str = "a+"):
        import os

        # fname is 'stdout' or 'stderr'
        if fname is None:
            return None

        if os.path.dirname(fname):
            os.makedirs(os.path.dirname(fname), exist_ok=True)
        fd = open(fname, mode)
        return fd

    def get_snippet(self, file_obj) -> str:
        file_obj.seek(0, 0)
        last_n_lines = file_obj.readlines()[-self.snippet_lines :]
        return "".join(last_n_lines)

    def get_and_close_streams(self, stdout, stderr) -> t.Tuple[str, str]:
        stdout_snippet = self.get_snippet(stdout)
        stderr_snippet = self.get_snippet(stderr)
        stdout.close()
        stderr.close()
        return stdout_snippet, stderr_snippet

    def execute_cmd_line(
        self,
        cmd: str,
        stdout: t.Optional[str] = None,
        stderr: t.Optional[str] = None,
        rundir: t.Optional[str] = None,
    ) -> BashResult:
        import os
        import subprocess

        run_dir = rundir or self.rundir

        if not run_dir:
            # run_dir takes priority over sandboxing
            if os.environ.get("GC_TASK_SANDBOX_DIR"):
                run_dir = os.environ["GC_TASK_SANDBOX_DIR"]
            else:
                if self.run_in_sandbox and os.environ.get("GC_TASK_UUID"):
                    run_dir = os.environ["GC_TASK_UUID"]
                    os.environ["GC_TASK_SANDBOX_DIR"] = os.path.join(
                        os.getcwd(), run_dir
                    )

        if run_dir:
            os.makedirs(run_dir, exist_ok=True)
            os.chdir(run_dir)

        stdout = (
            stdout or self.stdout or tempfile.NamedTemporaryFile(dir=os.getcwd()).name
        )
        stderr = (
            stderr or self.stdout or tempfile.NamedTemporaryFile(dir=os.getcwd()).name
        )

        std_out = self.open_std_fd(stdout)
        std_err = self.open_std_fd(stderr)
        exception_name = None

        """
        if std_err is not None:
            print(
                f"--> executable follows <--\n{cmd}\n--> end executable <--",
                file=std_err,
                flush=True,
            )
       """

        try:
            proc = subprocess.Popen(
                cmd,
                stdout=std_out,
                stderr=std_err,
                shell=True,
                executable="/bin/bash",
                close_fds=False,
            )
            proc.wait(timeout=self.walltime)
            returncode = proc.returncode
            if returncode != 0:
                exception_name = "subprocess.CalledProcessError"

        except subprocess.TimeoutExpired:
            # Returncode to match behavior of timeout bash command
            # https://man7.org/linux/man-pages/man1/timeout.1.html
            returncode = 124
            exception_name = "subprocess.TimeoutExpired"

        stdout_snippet, stderr_snippet = self.get_and_close_streams(std_out, std_err)

        return BashResult(
            cmd,
            stdout_snippet,
            stderr_snippet,
            returncode,
            exception_name=exception_name,
        )

    def __call__(
        self,
        stdout: t.Optional[str] = None,
        stderr: t.Optional[str] = None,
        rundir: t.Optional[str] = None,
        **kwargs,
    ) -> BashResult:
        """This method is passed from an executor to an endpoint to execute the
        BashFunction :

        .. code-block:: python

            bf = BashFunction("echo 'Hello'")
            future = executor.submit(bf)  # Invokes this method on an endpoint
            future.result()               # returns a BashResult

        Parameters
        ----------
        stdout: str|None
           file path to which stdout should be captured
           overrides stdout set at BashFunction declaration

        stderr: str|None
           file path to which stderr should be captures
           overrides stdout set at BashFunction declaration

        rundir: str|None
           directory within which the command should be executed
           overrides stdout set at BashFunction declaration

        **kwargs:
           arbitrary keyword args will be used to format the `cmd` string
           before execution

        Returns
        -------
        BashResult: BashResult
           Bash result object that encapsulates outputs from
           command execution
        """
        import copy

        # Copy to avoid mutating the class vars
        format_args = copy.copy(vars(self))
        format_args.update(kwargs)
        cmd_line = self.cmd.format(**format_args)
        return self.execute_cmd_line(
            cmd_line, stdout=stdout, stderr=stderr, rundir=rundir
        )
