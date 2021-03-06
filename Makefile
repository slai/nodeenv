.PHONY: deploy deploy-github deploy-pypi update-pypi clean tests

deploy-github:
	git tag `grep "nodeenv_version =" nodeenv.py | grep -o -E '[0-9]\.[0-9]\.[0-9]{1,2}'`
	git push --tags origin master

deploy-pypi:
	python setup.py sdist upload

update-pypi:
	python setup.py register

deploy: deploy-github deploy-pypi

clean:
	@rm -rf nodeenv.egg-info/
	@rm -rf dist/
	@rm -rf build/
	@rm -rf env/
	@rm -rf nodeenv/

test1:
	@echo " * test1: separate nodejs's env"
	@rm -rf env                           && \
		virtualenv --no-site-packages env && \
		. env/bin/activate                && \
		python setup.py install           && \
		rm -rf nodeenv                    && \
		nodeenv -j 4 nodeenv

test2:
	@echo " * test2: the same virtualenv's env, with 4 jobs"
	@rm -rf env                           && \
		virtualenv --no-site-packages env && \
		. env/bin/activate                && \
		python setup.py install           && \
		nodeenv -j 4 -p

test3:
	@echo " * test3: the same virtualenv's env, without any params"
	@rm -rf env                           && \
		virtualenv --no-site-packages env && \
		. env/bin/activate                && \
		python setup.py install           && \
		nodeenv -p

test4:
	@echo " * test4: separate nodejs's env for python3.2"
	@rm -rf env                                                  && \
		virtualenv --no-site-packages --python=python3.2 env     && \
		. env/bin/activate                                       && \
		python3.2 setup.py install                               && \
		nodeenv -j 4 -p

tests: clean test1 clean test2 clean test3 clean
