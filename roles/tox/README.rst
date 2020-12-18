Runs tox for a project

**Role Variables**

.. zuul:rolevar:: tox_environment

   Environment variables to pass in to the tox run.

.. zuul:rolevar:: tox_envlist

   Comma separated string with test environments tox should run.
   ``ALL`` runs all test environments while an empty string runs
   all test environments configured with ``envlist`` in tox.

.. zuul:rolevar:: tox_executable
   :default: tox

   Location of the tox executable.

.. zuul:rolevar:: tox_extra_args
   :default: -vv

   String of extra command line options to pass to tox.

.. zuul:rolevar:: tox_constraints_file

   Path to a pip constraints file. Will be provided to tox via
   ``TOX_CONSTRAINTS_FILE`` (deprecated but currently still supported
   name is ``UPPER_CONSTRAINTS_FILE``) environment variable if it
   exists.

.. zuul:rolevar:: tox_install_siblings
   :default: true

   Flag controlling whether to attempt to install python packages from any
   other source code repos zuul has checked out. Defaults to True.

.. zuul:rolevar:: tox_package_name

   Allows a user to setup the package name to be used by tox, over reading
   a setup.cfg file in the project.

.. zuul:rolevar:: zuul_work_dir
   :default: {{ zuul.project.src_dir }}

   Directory to run tox in.
