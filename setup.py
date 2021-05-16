#!/usr/bin/env python
"""Setup for stormaudio module."""
from setuptools import setup

def readme():
    """Return README file as a string."""
    with open('README.rst', 'r') as f:
        return f.read()

setup(
    name='stormaudio',
    version='0.1',
    author='Matthew Soldo',
    author_email='matt@soldo.org',
    url='https://github.com/mattsoldo/python-stormaudio',
    license="LICENSE",
    packages=['stormaudio'],
    scripts=[],
    description='Python API for controlling Anthem Receivers',
    long_description=readme(),
    classifiers=[
        'Development Status :: 5 - Production/Stable',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Operating System :: OS Independent',
        'Programming Language :: Python',
        'Topic :: Software Development :: Libraries :: Python Modules',
    ],
    include_package_data=True,
    zip_safe=True,

    entry_points={
        'console_scripts': [ 'stormaudio_monitor = stormaudio.tools:monitor', ]
    }
)
