#!/bin/bash
# Script to run multiple simulations 5 by 5 until the desired number is reached

helpFunction()
{
   echo ""
   echo "Usage: $0 -n num_sims"
   echo -e "\t-n Number of simulations to run"
   echo -e "\t-c Net configuration file"
   echo -e "\t-s Number of simulation steps"
   echo -e "\t-w Number of steps for simulation to wait before learning"
   echo -e "\t-r C2I communication success rate step (100 needs to be multiple of this parameter)"
   exit 1
}

# Get parameters from args
while getopts "n:c:s:w:r:" opt
do
   case "$opt" in
      n ) num_sims="$OPTARG" ;;
      c ) net_file="$OPTARG" ;;
      s ) steps="$OPTARG" ;;
      w ) wait_learn="$OPTARG" ;;
      r ) rate_step="$OPTARG" ;;
      ? ) helpFunction ;;
   esac
done

# Print helpFunction in case parameters are empty
if [ -z "$num_sims" ] || [ -z "$net_file" ] || [ -z "$steps" ] || [ -z "$wait_learn" ]
then
   echo "One or more arguments are empty";
   helpFunction
fi

if [ -z "$rate_step" ] 
then
   rate_step=100
elif [ 100 % rate_step != 0 ]
then
   echo "Parameter -rs value not allowed"
   helpFunction
fi

# Begin script in case all parameters are correct
now=$(date +"%d/%m/%Y - %H:%M")
echo "Script will run $num_sims in total"
echo "Starting simulations in background..."
echo -e "Starting time at \t\t\t $now"

for i in $(eval echo "{0..100..$rate_step}")
do
   succ_rate=$(bc -l <<<"scale=2;$i/100")
   echo ""
   echo "Starting to run simulations with communication success rate $succ_rate"
   for j in $(eval echo "{5..$num_sims..5}")
   do  
      python3 main.py -c $net_file -s $steps -w $wait_learn -r $succ_rate > /dev/null 2>&1 &
      sleep 60 && python3 main.py -c $net_file -s $steps -w $wait_learn -r $succ_rate > /dev/null 2>&1 &
      sleep 120 && python3 main.py -c $net_file -s $steps -w $wait_learn -r $succ_rate > /dev/null 2>&1 &
      sleep 180 && python3 main.py -c $net_file -s $steps -w $wait_learn -r $succ_rate > /dev/null 2>&1 &
      sleep 240 && python3 main.py -c $net_file -s $steps -w $wait_learn -r $succ_rate > /dev/null 2>&1 &
      wait
      now=$(date +"%d/%m/%Y - %H:%M")
      echo -e "Finished running $j with $i C2I success rate simulations at \t $now"
   done
done
echo ""
echo "Done"
