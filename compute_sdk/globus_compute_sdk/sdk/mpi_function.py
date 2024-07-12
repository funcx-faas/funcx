import typing as t

from .bash_function import BashFunction


class MPIFunction(BashFunction):
    """MPIFunction extends BashFunction, as a thin wrapper that adds an
    MPI launcher prefix to the BashFunction command.
    """

    def __call__(
        self,
        stdout: t.Optional[str] = None,
        stderr: t.Optional[str] = None,
        rundir: t.Optional[str] = None,
        **kwargs,
    ):
        import copy

        self.stdout = stdout or self.stdout
        self.stderr = stderr or self.stderr
        self.rundir = rundir or self.rundir

        # Copy to avoid mutating the class vars
        format_args = copy.copy(vars(self))
        format_args.update(kwargs)
        cmd_line = "$PARSL_MPI_PREFIX " + self.cmd.format(**format_args)
        return self.execute_cmd_line(cmd_line)
