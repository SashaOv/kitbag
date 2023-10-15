from setuptools import setup, find_packages
from codecs import open
from os import path

current_directory = path.abspath(path.dirname(__file__))

readme_file= 'README.rst'

def read_long_description():
  with open(path.join(current_directory, readme_file), encoding='utf-8') as input:
    return input.read()

setup(
  name='connon',
  version= '0.1',

  description='Keep my broken wireless connection on',
  long_description=read_long_description(),
  url='https://bitbucket.org/sashao/kitbag',

  author= 'Sasha Ovsankin',
  author_email= 'sasha@ovsankin.com',
  license= 'Apache License, Version 2.0',
  packages= find_packages(),
  package_data={ '': [readme_file, 'LICENSE'] },
  install_requires= [ ],
  entry_points={
    'console_scripts': [
      'connon = connon.cli:cli'
    ],
  }
)

