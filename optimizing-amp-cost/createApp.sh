#!/bin/bash

if [ $# -ne 1 ]
then
    echo "Please provide number of deployments to be deployed."
    exit 1
fi

kubectl create namespace sample-nginx

for i in $(seq 1 $1)
do
  export N=$i
  envsubst < nginx-template.yaml > nginx.yaml
  kubectl apply -f nginx.yaml
done