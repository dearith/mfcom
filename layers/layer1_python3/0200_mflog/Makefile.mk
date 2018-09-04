include ../../../adm/root.mk
include $(MFEXT_HOME)/share/subdir_root.mk

EGG=mflog-0.0.0-py$(PYTHON_SHORT_VERSION).egg

clean:: pythonclean

all:: dist/$(EGG)

dist/$(EGG):
	python setup.py install --prefix=$(MFCOM_HOME)/opt/python$(METWORK_PYTHON_MODE)

test:
	flake8.sh --exclude=build .
	find . -name "*.py" ! -path './build/*' -print0 |xargs -0 pylint.sh --errors-only 
	cd tests && nosetests.sh .

coverage:
	cd tests && nosetests.sh --with-coverage --cover-package=mflog --cover-erase --cover-html --cover-html-dir=coverage .
