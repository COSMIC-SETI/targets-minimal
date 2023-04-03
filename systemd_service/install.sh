#!/bin/bash 

if [[ $EUID > 0 ]]
then 
  echo "Please run with super-user privileges"
  exit 1
else
	cp ./target_selector.service /etc/systemd/system/

	systemctl disable target_selector.service
	systemctl daemon-reload
	systemctl enable target_selector.service
fi