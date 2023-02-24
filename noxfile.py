import nox
import os

@nox.session(python=["3.8"])
def flake8(session):
    session.install("flake8", "flake8-bugbear", "flake8-import-order",
                    "flake8-bandit")
    session.run("flake8", "mango")


@nox.session(python=["3.8"])
def pylint(session):
    session.install("pylint")
    session.run("pylint", "mango")


@nox.session(python=["3.8"])
def pytype(session):
    session.install("pytype")
    session.run("pytype", "-d import-error", "mango")


@nox.session(python=["3.8"])
def safety(session):
    session.install("safety")
    session.run("safety", "check")


@nox.session(python=["3.8"])
def unit_tests(session):
    session.install("-r", "requirements.txt")
    session.install("-e", ".")
    session.install("pytest", "pytest-cov")
    os.chdir("tests/unit_tests")
    session.run("pytest", "--cov", ".")


@nox.session(python=["3.8"])
def integration_tests(session):
    session.install("-r", "requirements.txt")
    session.install("-e", ".")
    session.install("pytest", "pytest-cov")
    os.chdir("tests/integration_tests")
    session.run("pytest", "--cov", ".")