import statistics





# initializing list
test_list = [85.16, 84.86, 85.22, 83.66, 85.32]
print('sum:', sum(test_list))
average = round(sum(test_list)/len(test_list), 2)
res = round(statistics.pstdev(test_list),2)

print(str(average)+'/'+str(res))

'''
2+0.9 source without seed 32
85.08/0.11
'''
