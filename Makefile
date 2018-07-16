include common.mk

all:
	@echo Looking good!
	@echo '`make deploy`' deploys the AWS Lambda functions
	@echo '`make terraform`' creates the necessary cloud infrastructure that the Lambda functions depend on

terraform:
	$(MAKE) -C terraform

deploy:
	$(MAKE) -C lambdas

clean:
	for d in lambdas terraform; do $(MAKE) -C $$d clean; done

.PHONY: all terraform deploy clean