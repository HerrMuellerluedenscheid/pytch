Vocal Trainer
===============

[![Build Status](https://travis-ci.org/HerrMuellerluedenscheid/pytch.svg?branch=master)](https://travis-ci.org/HerrMuellerluedenscheid/pytch)

## Prerequisites

Pytch requires python3, as well as the following libraries:

- python headers
- scipy
- numpy
- PyQt5
- aubio
- pyaudio
- portaudio
- libportaudio2

```
sudo apt-get install portaudio19-dev libportaudio2
```

Optional but recommended is the installation of PyQt5-OpenGL bindings.
Help can be found here: http://pyqt.sourceforge.net/Docs/PyQt5/installation.html

## Download and Installation

Download the project
```
git clone https://github.com/HerrMuellerluedenscheid/pytch.git
cd pytch
```

### pip

```pip install .```

### Miniconda/Anaconda:

```
conda env create -f environment.yml
source activate pytch
sudo python setup.py install
```

###  From source

Go to a directory where you keep your source codes and clone the project:
```
git clone https://github.com/HerrMuellerluedenscheid/pytch.git
cd pytch
sudo python setup.py install
```

# Invocation
Open a terminal, type
```
pytch
```
hit return and sing!
