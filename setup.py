import setuptools

requires = [
    'numpy >= 1.18.1',
    'redis >= 3.4.1',
    'pandas >= 1.4.2',
    'PyYAML >= 6.0',
    'scipy >= 1.8.0',
    'SQLAlchemy >= 1.4.35'
    ]

setuptools.setup(
    name = 'targets_minimal',
    version = '1.0.1',
    url = 'https://github.com/danielczech/targets-minimal',
    license = 'MIT',
    author = 'Daniel Czech',
    author_email = 'danielc@berkeley.edu',
    description = 'Minimal target selector for Breakthrough Listen\'s commensal observing',
    packages = [
        'targets_minimal',
        ],
    install_requires=requires,
    entry_points = {
        'console_scripts':[
            'targets_minimal = targets_minimal.cli:cli',
            ]
        },
    )
