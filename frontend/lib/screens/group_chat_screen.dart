import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../models/group.dart';
import '../providers/group_provider.dart';

/// 群聊界面（M2）：多个角色依次发言
class GroupChatScreen extends StatefulWidget {
  const GroupChatScreen({super.key});

  @override
  State<GroupChatScreen> createState() => _GroupChatScreenState();
}

class _GroupChatScreenState extends State<GroupChatScreen> {
  final TextEditingController _controller = TextEditingController();
  final ScrollController _scrollController = ScrollController();

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  void _scrollToBottom() {
    WidgetsBinding.instance.addPostFrameCallback((_) {
      if (_scrollController.hasClients) {
        _scrollController.animateTo(
          _scrollController.position.maxScrollExtent,
          duration: const Duration(milliseconds: 200),
          curve: Curves.easeOut,
        );
      }
    });
  }

  /// 选择「让谁回应」：自由轮询 or 指定某个角色
  Future<void> _pickSpeaker(GroupProvider group) async {
    final info = group.current;
    if (info == null) return;

    const allKey = '__all__';
    final selected = await showModalBottomSheet<String>(
      context: context,
      builder: (ctx) => SafeArea(
        child: SingleChildScrollView(
          child: Column(
            mainAxisSize: MainAxisSize.min,
            children: [
              const Padding(
                padding: EdgeInsets.symmetric(vertical: 12),
                child: Text(
                  '让谁回应？',
                  style: TextStyle(fontSize: 15, fontWeight: FontWeight.w600),
                ),
              ),
              ListTile(
                leading: const Icon(Icons.groups),
                title: const Text('自由轮询'),
                subtitle: const Text('大家按顺序一起聊'),
                trailing: group.targetCharacterId == null
                    ? const Icon(Icons.check)
                    : null,
                onTap: () => Navigator.pop(ctx, allKey),
              ),
              ...info.members.map((m) => ListTile(
                    leading: CircleAvatar(
                      child: Text(m.name.isEmpty ? '?' : m.name[0]),
                    ),
                    title: Text(m.name),
                    subtitle: m.description.isEmpty
                        ? null
                        : Text(
                            m.description,
                            maxLines: 1,
                            overflow: TextOverflow.ellipsis,
                          ),
                    trailing: group.targetCharacterId == m.characterId
                        ? const Icon(Icons.check)
                        : null,
                    onTap: () => Navigator.pop(ctx, m.characterId),
                  )),
            ],
          ),
        ),
      ),
    );

    if (selected == null) return;
    group.setReplyTarget(selected == allKey ? null : selected);
  }

  @override
  Widget build(BuildContext context) {
    return Consumer<GroupProvider>(
      builder: (context, group, child) {
        final info = group.current;
        _scrollToBottom();

        return PopScope(
          // 离开群聊时触发按角色视角的记忆蒸馏，并断开实时连接（不阻塞返回）
          onPopInvokedWithResult: (didPop, result) {
            if (didPop) {
              group.endGroup();
              group.closeGroup();
            }
          },
          child: Scaffold(
          appBar: AppBar(
            title: Text(info?.name ?? '群聊'),
            centerTitle: true,
            actions: [
              IconButton(
                tooltip: '选择谁回应',
                onPressed: info == null ? null : () => _pickSpeaker(group),
                icon: Icon(
                  group.targetCharacterId == null
                      ? Icons.record_voice_over_outlined
                      : Icons.record_voice_over,
                ),
              ),
            ],
            bottom: PreferredSize(
              preferredSize: const Size.fromHeight(20),
              child: Padding(
                padding: const EdgeInsets.only(bottom: 6),
                child: Text(
                  info == null
                      ? ''
                      : info.members.map((m) => m.name).join(' · '),
                  style: const TextStyle(fontSize: 11, color: Colors.grey),
                ),
              ),
            ),
          ),
          body: Column(
            children: [
              Expanded(
                child: group.messages.isEmpty
                    ? const Center(
                        child: Text(
                          '说点什么，让大家聊起来～',
                          style: TextStyle(color: Colors.grey),
                        ),
                      )
                    : ListView.builder(
                        controller: _scrollController,
                        padding: const EdgeInsets.symmetric(vertical: 8),
                        itemCount: group.messages.length,
                        itemBuilder: (context, index) {
                          return _GroupBubble(message: group.messages[index]);
                        },
                      ),
              ),
              if (group.error.isNotEmpty)
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.all(8),
                  color: Colors.red.shade50,
                  child: Text(
                    group.error,
                    style: const TextStyle(color: Colors.red, fontSize: 12),
                    textAlign: TextAlign.center,
                  ),
                ),
              // 指定角色回应时的提示条
              if (group.targetName.isNotEmpty)
                Container(
                  width: double.infinity,
                  padding: const EdgeInsets.symmetric(horizontal: 16, vertical: 4),
                  color: Theme.of(context).colorScheme.secondaryContainer,
                  child: Row(
                    children: [
                      Icon(
                        Icons.alternate_email,
                        size: 16,
                        color: Theme.of(context).colorScheme.primary,
                      ),
                      const SizedBox(width: 6),
                      Expanded(
                        child: Text(
                          '仅 ${group.targetName} 回应',
                          style: const TextStyle(fontSize: 12),
                        ),
                      ),
                      TextButton(
                        onPressed: group.isStreaming
                            ? null
                            : () => group.setReplyTarget(null),
                        child: const Text('改为自由轮询'),
                      ),
                    ],
                  ),
                ),
              _buildInputBar(group),
            ],
          ),
          ),
        );
      },
    );
  }

  Widget _buildInputBar(GroupProvider group) {
    // WebSocket 已连接时可以边听边插话，未连接时沿用 SSE 的「发言中禁止输入」
    final canType = !group.isStreaming || group.wsConnected;
    return Container(
      padding: const EdgeInsets.symmetric(horizontal: 8, vertical: 8),
      decoration: BoxDecoration(
        color: Theme.of(context).colorScheme.surface,
        boxShadow: [
          BoxShadow(
            color: Colors.black.withValues(alpha: 0.05),
            blurRadius: 4,
            offset: const Offset(0, -2),
          ),
        ],
      ),
      child: SafeArea(
        child: Row(
          children: [
            Expanded(
              child: TextField(
                controller: _controller,
                enabled: canType,
                decoration: InputDecoration(
                  hintText: group.isStreaming
                      ? (group.wsConnected
                          ? '发送会打断当前发言…'
                          : '其他人正在发言…')
                      : '在群里说点什么…',
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(24),
                    borderSide: BorderSide.none,
                  ),
                  filled: true,
                  fillColor: Theme.of(context).colorScheme.surfaceContainerHighest,
                  contentPadding:
                      const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                ),
                maxLines: null,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => _send(group),
              ),
            ),
            const SizedBox(width: 8),
            IconButton(
              onPressed: canType ? () => _send(group) : null,
              icon: Icon(
                Icons.send,
                color: canType
                    ? Theme.of(context).colorScheme.primary
                    : Colors.grey,
              ),
            ),
          ],
        ),
      ),
    );
  }

  void _send(GroupProvider group) {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    _controller.clear();
    group.sendMessage(text);
  }
}

/// 群聊消息气泡：用户靠右，角色靠左并显示发言人
class _GroupBubble extends StatelessWidget {
  final GroupMessage message;

  const _GroupBubble({required this.message});

  static const _colors = [
    Color(0xFF7C4DFF),
    Color(0xFF00897B),
    Color(0xFFEF6C00),
    Color(0xFF1E88E5),
  ];

  Color get _color {
    if (message.characterId.isEmpty) return _colors.first;
    final hash = message.characterId.codeUnits.fold<int>(0, (a, b) => a + b);
    return _colors[hash % _colors.length];
  }

  @override
  Widget build(BuildContext context) {
    if (message.isUser) {
      return Align(
        alignment: Alignment.centerRight,
        child: Container(
          margin: const EdgeInsets.symmetric(vertical: 4, horizontal: 12),
          padding: const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
          constraints: BoxConstraints(
            maxWidth: MediaQuery.of(context).size.width * 0.72,
          ),
          decoration: BoxDecoration(
            color: Theme.of(context).colorScheme.primary,
            borderRadius: BorderRadius.circular(16),
          ),
          child: Text(
            message.content,
            style: const TextStyle(color: Colors.white, fontSize: 15),
          ),
        ),
      );
    }

    return Padding(
      padding: const EdgeInsets.symmetric(vertical: 6, horizontal: 12),
      child: Row(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: [
          CircleAvatar(
            radius: 16,
            backgroundColor: _color.withValues(alpha: 0.15),
            child: Text(
              message.name.isEmpty ? '?' : message.name[0],
              style: TextStyle(color: _color, fontSize: 13),
            ),
          ),
          const SizedBox(width: 8),
          Flexible(
            child: Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: [
                Text(
                  message.name,
                  style: TextStyle(
                    fontSize: 12,
                    color: _color,
                    fontWeight: FontWeight.w600,
                  ),
                ),
                const SizedBox(height: 4),
                Container(
                  padding:
                      const EdgeInsets.symmetric(horizontal: 14, vertical: 10),
                  constraints: BoxConstraints(
                    maxWidth: MediaQuery.of(context).size.width * 0.66,
                  ),
                  decoration: BoxDecoration(
                    color: Theme.of(context).colorScheme.surfaceContainerHighest,
                    borderRadius: const BorderRadius.only(
                      topLeft: Radius.circular(4),
                      topRight: Radius.circular(16),
                      bottomLeft: Radius.circular(16),
                      bottomRight: Radius.circular(16),
                    ),
                  ),
                  child: Text(
                    message.content.isEmpty && message.isStreaming
                        ? '...'
                        : message.content,
                    style: const TextStyle(fontSize: 15),
                  ),
                ),
              ],
            ),
          ),
        ],
      ),
    );
  }
}
