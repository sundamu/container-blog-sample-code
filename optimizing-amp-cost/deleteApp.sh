#!/bin/bash

if [ $# -ne 1 ]
then
    echo "Please provide number of deployments to be deleted."
    exit 1
fi

for i in $(seq 1 $1)
do
  export N=$i
  envsubst < nginx-template.yaml > nginx.yaml
  kubectl delete -f nginx.yaml
done

kubectl delete namespace sample-nginx