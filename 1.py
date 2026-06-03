import matplotlib.pyplot as plt
import numpy as np

# 1. 模拟长尾数据（实际论文中替换为你的数据集类别统计）
# 比如：ImageNet-LT, Long-Tailed CIFAR, 或 LVIS 数据集
num_classes = 100
# 使用幂律分布（Power Law）模拟长尾特征
class_indices = np.arange(num_classes)
sample_counts = 5000 / (class_indices + 1)**0.8  

# 排序（确保是从大到小降序排列）
sorted_indices = np.argsort(sample_counts)[::-1]
sorted_counts = sample_counts[sorted_indices]

# 2. 开始画图
fig, ax1 = plt.subplots(figsize=(8, 4.5), dpi=300)

# 绘制柱状图（通常用学术界偏爱的莫兰迪色、深蓝色或灰色）
color_head = '#1f77b4' # 头部类颜色
bars = ax1.bar(range(num_classes), sorted_counts, width=0.8, color=color_head, alpha=0.85)

# 3. 美化 Y 轴：如果长尾太严重，建议开启对数坐标
# ax1.set_yscale('log') 

ax1.set_xlabel('Class Index (Sorted by frequency)', fontsize=12, fontweight='bold')
ax1.set_ylabel('Number of Samples', fontsize=12, fontweight='bold')
ax1.set_title('Long-Tailed Class Distribution', fontsize=14, fontweight='bold')

# 4. 区分 Many-shot, Medium-shot, Few-shot（顶会论文常见画法）
# 比如前 30% 为 Head，中间 40% 为 Mid，最后 30% 为 Tail
ax1.axvline(x=30, color='gray', linestyle='--', alpha=0.7)
ax1.axvline(x=70, color='gray', linestyle='--', alpha=0.7)

# 给不同区域上方加文字标注
ax1.text(10, max(sorted_counts)*0.8, 'Many-shot\n(Head)', ha='center', color='#d62728', weight='bold')
ax1.text(50, max(sorted_counts)*0.5, 'Medium-shot\n(Mid)', ha='center', color='#bcbd22', weight='bold')
ax1.text(85, max(sorted_counts)*0.2, 'Few-shot\n(Tail)', ha='center', color='#2ca02c', weight='bold')

# 5. 调整细节，去掉上方和右方的边框（符合现代学术审美）
ax1.spines['top'].set_visible(False)
ax1.spines['right'].set_visible(False)

plt.tight_layout()
plt.savefig('long_tail_distribution.pdf', bbox_inches='tight') # 保存为矢量图 PDF
plt.show()