import statistics





# initializing list
test_list = [83.52, 83.16, 83.26, 84.92, 84.22]
print('sum:', sum(test_list))
average = round(sum(test_list)/len(test_list), 2)
res = round(statistics.pstdev(test_list),2)

print(str(average)+'/'+str(res))
