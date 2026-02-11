"""
Octopoid Python SDK
API client for Octopoid v2.0 server
"""

from setuptools import setup, find_packages

setup(
    name='octopoid-sdk',
    version='2.0.0',
    description='Python SDK for Octopoid v2.0 API',
    author='Octopoid Team',
    author_email='support@octopoid.dev',
    url='https://github.com/maxthelion/octopoid',
    packages=find_packages(),
    install_requires=[
        'requests>=2.31.0',
    ],
    python_requires='>=3.8',
    classifiers=[
        'Development Status :: 4 - Beta',
        'Intended Audience :: Developers',
        'License :: OSI Approved :: MIT License',
        'Programming Language :: Python :: 3',
    ],
)
