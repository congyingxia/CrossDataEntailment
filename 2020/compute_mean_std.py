import statistics





# initializing list
test_list = [85.22, 84.99, 84.19, 84.76, 85.29]
print('sum:', sum(test_list))
average = round(sum(test_list)/len(test_list), 2)
res = round(statistics.pstdev(test_list),2)

print(str(average)+'/'+str(res))

'''
dev acc: 0.8411552346570397  max_dev_acc: 0.8411552346570397
dev acc: 0.8411552346570397  max_dev_acc: 0.8411552346570397
dev acc: 0.8339350180505415  max_dev_acc: 0.8411552346570397
dev acc: 0.8375451263537906  max_dev_acc: 0.8411552346570397
dev acc: 0.8375451263537906  max_dev_acc: 0.8411552346570397
dev acc: 0.8375451263537906  max_dev_acc: 0.8411552346570397
dev acc: 0.8447653429602888  max_dev_acc: 0.8447653429602888
dev acc: 0.8339350180505415  max_dev_acc: 0.8447653429602888
dev acc: 0.8411552346570397  max_dev_acc: 0.8447653429602888
dev acc: 0.8411552346570397  max_dev_acc: 0.8447653429602888
dev acc: 0.8339350180505415  max_dev_acc: 0.8447653429602888
dev acc: 0.8447653429602888  max_dev_acc: 0.8447653429602888
dev acc: 0.8339350180505415  max_dev_acc: 0.8447653429602888
dev acc: 0.8375451263537906  max_dev_acc: 0.8447653429602888
dev acc: 0.8411552346570397  max_dev_acc: 0.8447653429602888
dev acc: 0.8303249097472925  max_dev_acc: 0.8447653429602888
dev acc: 0.8375451263537906  max_dev_acc: 0.8447653429602888
dev acc: 0.8303249097472925  max_dev_acc: 0.8447653429602888
dev acc: 0.8411552346570397  max_dev_acc: 0.8447653429602888
dev acc: 0.8339350180505415  max_dev_acc: 0.8447653429602888

dev acc: 0.8375451263537906  max_dev_acc: 0.8375451263537906
dev acc: 0.8339350180505415  max_dev_acc: 0.8375451263537906
dev acc: 0.8339350180505415  max_dev_acc: 0.8375451263537906
dev acc: 0.8375451263537906  max_dev_acc: 0.8375451263537906
dev acc: 0.8375451263537906  max_dev_acc: 0.8375451263537906
dev acc: 0.8339350180505415  max_dev_acc: 0.8375451263537906
dev acc: 0.8339350180505415  max_dev_acc: 0.8375451263537906
dev acc: 0.8339350180505415  max_dev_acc: 0.8375451263537906
dev acc: 0.8375451263537906  max_dev_acc: 0.8375451263537906
dev acc: 0.8375451263537906  max_dev_acc: 0.8375451263537906
dev acc: 0.8375451263537906  max_dev_acc: 0.8375451263537906
dev acc: 0.8339350180505415  max_dev_acc: 0.8375451263537906
dev acc: 0.8339350180505415  max_dev_acc: 0.8375451263537906
dev acc: 0.8303249097472925  max_dev_acc: 0.8375451263537906
dev acc: 0.8267148014440433  max_dev_acc: 0.8375451263537906
dev acc: 0.8339350180505415  max_dev_acc: 0.8375451263537906
dev acc: 0.8339350180505415  max_dev_acc: 0.8375451263537906
dev acc: 0.8303249097472925  max_dev_acc: 0.8375451263537906
dev acc: 0.8303249097472925  max_dev_acc: 0.8375451263537906
dev acc: 0.8339350180505415  max_dev_acc: 0.8375451263537906

'''
