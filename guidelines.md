As we mainly work in a critical domain, we set great value on code quality not only to ensure correctness, but also to improve readability and maintainability. To reach this goal we have to set some standards regarding the development process and test quality. 

# Quickstart (for mango dev-members)
In mango it is not possible to directly push on dev or master. Both branches are protected and changes can only be merged using a gitlab merge-request. So when you work on a feature, the typicial process would be to create a feature-branch. When you are finished you just have to create a merge-request, pass the CI/CD pipeline, make a maintainer review the changes and you are ready to merge!

# CI/CD
To monitor the quality, issues found by linters, code coverage and correctness of the tests, we need an automated process triggered on every branch for every commit. That is done by our CI/CD pipeline. You can find the code-coverage, linting and the test-report there.

Continous Deployment to PyPi is planned but not ready yet.

# Quality guidelines

## Tests
We understand testing as part of the normal software development process. That means that we write automated tests for every new feature, every fixed bug. Furthermore it is necessary to ensure that we test our code integratively **and** with unit tests. As a consequence every feature have to be part of an integration test should have its own unit tests. Doing that we can be sure that the code is working correctly when integrated in a typical use case and when executed "standalone".

### Unit Tests
A unit test is a test for a smallest possible testable part of the code. This is often a method or a class, in case of mango it can be a bit bigger though, because asyncio is heavility used.

### Integration Tests
An integration test is everything what aims to test more than one unit. 

### Coverage
We aim to reach a code coverage of > 90%. Currently we measure the **statement** coverage at the 'check' job of the CI/CD pipeline.

## Reviews
Tests are great but do not lead to better readability and maintainability. One part that will is the review process. In mango we came to the undestanding that we want to review **every** change, which should be merged into the development branch. There are no exceptions to this. The idea is not only to check the code for errors, bad smells and security flaws, its part of generating a common understanding of good coding. 

## Linting
Another approach to improve the code quality is static code analysis, or better known as linting. Linting is an easy to setup possibility to make sure that a certain code standard is fullfiled. There are many useful rules, which can be checked automatically, so we have another line of defence and spare some time when reviewing. 
