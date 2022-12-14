Ensure sphinx is installed

Installs sphinx. Also installs any dependencies needed in the first of
doc/requirements.txt, releasenotes/requirements.txt and
test-requirements.txt to be found.

All pip installs are done with a provided constraints file, if given.

**Role Variables**

.. zuul:rolevar:: constraints_file

   Optional path to a pip constraints file for installing python libraries.

.. zuul:rolevar:: doc_building_packages
   :default: []

   List of python packages to install for building docs. The default
   package list is based on the python version in use.

.. zuul:rolevar:: doc_building_extra_packages
   :default: []

   List of python additional packages to install for building docs.
   By default this list is empty.

.. zuul:rolevar:: sphinx_python
   :default: python3

   **Deprecated**
   Version of python to use, only supports ``python3``.

.. zuul:rolevar:: zuul_work_virtualenv
   :default: ~/.venv

   Virtualenv location in which to install things.

.. zuul:rolevar:: zuul_work_dir
   :default: {{ zuul.project.src_dir }}

   Directory to operate in.
