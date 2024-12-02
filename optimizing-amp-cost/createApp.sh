#!/bin/bash

kubectl create namespace sample-nginx

for i in {1..60}
do
  export N=$i
  envsubst < nginx-template.yaml > nginx.yaml
  kubectl apply -f nginx.yaml
done