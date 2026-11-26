import 'package:flutter/material.dart';
import 'package:provider/provider.dart';
import '../providers/chat_provider.dart';
import '../providers/voice_provider.dart';
import '../widgets/message_bubble.dart';

/// 聊天主界面
class ChatScreen extends StatefulWidget {
  const ChatScreen({super.key});

  @override
  State<ChatScreen> createState() => _ChatScreenState();
}

class _ChatScreenState extends State<ChatScreen> {
  final TextEditingController _controller = TextEditingController();
  final ScrollController _scrollController = ScrollController();
  bool _settingsOpen = false;

  /// 录音过程中累积的识别文本（最终结果用于回填输入框）
  String _voiceText = '';

  @override
  void initState() {
    super.initState();
    // 进入聊天页时恢复该角色最近一次会话，并初始化语音能力
    WidgetsBinding.instance.addPostFrameCallback((_) async {
      if (!mounted) return;
      context.read<ChatProvider>().loadHistory();
      await context.read<VoiceProvider>().init();
    });
  }

  @override
  void dispose() {
    _controller.dispose();
    _scrollController.dispose();
    super.dispose();
  }

  /// 按住说话：开始识别
  Future<void> _startVoice() async {
    final voice = context.read<VoiceProvider>();
    if (voice.isRecording) return;
    _voiceText = '';
    final started = await voice.startListening(
      onText: (text, isFinal) {
        _voiceText = text;
        if (text.isNotEmpty) {
          _controller.text = text;
          _controller.selection = TextSelection.fromPosition(
            TextPosition(offset: text.length),
          );
        }
      },
      onDone: () {
        if (_voiceText.isEmpty && mounted) {
          ScaffoldMessenger.of(context).showSnackBar(
            const SnackBar(content: Text('没有听到内容，请再说一次')),
          );
        }
      },
    );
    if (!started && mounted) {
      ScaffoldMessenger.of(context).showSnackBar(
        SnackBar(content: Text(voice.error.isEmpty ? '语音识别不可用' : voice.error)),
      );
    }
  }

  /// 松手：结束识别
  Future<void> _stopVoice() async {
    final voice = context.read<VoiceProvider>();
    if (!voice.isRecording) return;
    await voice.stopListening();
  }

  /// 设备缺少语音识别服务时的引导
  void _showVoiceUnavailable() {
    ScaffoldMessenger.of(context).showSnackBar(
      const SnackBar(
        content: Text('当前设备没有可用的语音识别服务，可先使用文字输入；'
            '也可在后端启用本地 whisper / 云端 ASR 后使用录音识别'),
        duration: Duration(seconds: 4),
      ),
    );
  }

  /// 语音状态条（录音中 / 识别中 / 朗读中）
  Widget _buildVoiceBar() {
    final voice = context.watch<VoiceProvider>();
    if (voice.state == VoiceState.idle) return const SizedBox.shrink();

    final isRecording = voice.state == VoiceState.recording;
    final isRecognizing = voice.state == VoiceState.recognizing;
    // 系统识别返回 dB 值，归一到 0~1；后端录音没有音量回调
    final showLevel = isRecording && !voice.usingBackendAsr;
    final level = ((voice.soundLevel + 2) / 12).clamp(0.0, 1.0);

    return Container(
      width: double.infinity,
      padding: const EdgeInsets.symmetric(vertical: 8, horizontal: 16),
      color: isRecording
          ? Theme.of(context).colorScheme.primaryContainer
          : Theme.of(context).colorScheme.surfaceContainerHighest,
      child: Row(
        mainAxisAlignment: MainAxisAlignment.center,
        children: [
          Icon(
            isRecognizing
                ? Icons.hourglass_top
                : (isRecording ? Icons.graphic_eq : Icons.volume_up),
            size: 18,
            color: Theme.of(context).colorScheme.primary,
          ),
          const SizedBox(width: 8),
          Text(
            isRecognizing
                ? '正在识别…'
                : (isRecording ? '正在听… 松手结束' : '正在朗读…'),
            style: const TextStyle(fontSize: 13),
          ),
          if (isRecognizing) ...[
            const SizedBox(width: 12),
            const SizedBox(
              width: 16,
              height: 16,
              child: CircularProgressIndicator(strokeWidth: 2),
            ),
          ] else if (showLevel) ...[
            const SizedBox(width: 12),
            SizedBox(
              width: 60,
              height: 6,
              child: ClipRRect(
                borderRadius: BorderRadius.circular(3),
                child: LinearProgressIndicator(
                  value: level,
                  minHeight: 6,
                  backgroundColor: Colors.white54,
                ),
              ),
            ),
          ] else if (isRecording) ...[
            const SizedBox(width: 8),
            const Text(
              '松手后自动转文字',
              style: TextStyle(fontSize: 12, color: Colors.grey),
            ),
          ] else ...[
            const SizedBox(width: 12),
            TextButton(
              onPressed: () => context.read<VoiceProvider>().stopSpeaking(),
              child: const Text('停止'),
            ),
          ],
        ],
      ),
    );
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

  @override
  Widget build(BuildContext context) {
    return Consumer<ChatProvider>(
      builder: (context, chat, child) {
        final voice = context.watch<VoiceProvider>();
        // 消息更新时自动滚动到底部
        WidgetsBinding.instance.addPostFrameCallback((_) => _scrollToBottom());

        return PopScope(
          // 离开聊天页时触发记忆蒸馏（不阻塞返回，也不清空本地消息）
          onPopInvokedWithResult: (didPop, result) {
            if (didPop) chat.endSession();
          },
          child: Scaffold(
          appBar: AppBar(
            title: Text(chat.characterName.isEmpty ? 'AI 角色扮演' : chat.characterName),
            centerTitle: true,
          ),
          body: GestureDetector(
            onHorizontalDragUpdate: (details) {
              // 左滑打开设置面板
              if (details.delta.dx < -2 && !_settingsOpen) {
                setState(() => _settingsOpen = true);
              } else if (details.delta.dx > 2 && _settingsOpen) {
                setState(() => _settingsOpen = false);
              }
            },
            child: Stack(
              children: [
                Column(
                  children: [
                    // 消息列表
                    Expanded(
                      child: chat.messages.isEmpty
                          ? const Center(
                              child: Text(
                                '开始和角色对话吧～',
                                style: TextStyle(color: Colors.grey),
                              ),
                            )
                          : ListView.builder(
                              controller: _scrollController,
                              padding: const EdgeInsets.symmetric(vertical: 8),
                              itemCount: chat.messages.length,
                              itemBuilder: (context, index) {
                                final message = chat.messages[index];
                                return MessageBubble(
                                  message: message,
                                  // 设备无 TTS 能力时不显示朗读按钮，避免点了没反应
                                  onSpeak: voice.ttsAvailable
                                      ? () => voice.speak(message.content)
                                      : null,
                                );
                              },
                            ),
                    ),
                    // 语音状态提示
                    _buildVoiceBar(),
                    // 错误提示
                    if (chat.error.isNotEmpty)
                      Container(
                        width: double.infinity,
                        padding: const EdgeInsets.all(8),
                        color: Colors.red.shade50,
                        child: Text(
                          chat.error,
                          style: const TextStyle(color: Colors.red, fontSize: 12),
                          textAlign: TextAlign.center,
                        ),
                      ),
                    // 输入栏
                    _buildInputBar(chat),
                  ],
                ),
                // 设置面板（左滑）
                _buildSettingsPanel(chat),
              ],
            ),
          ),
          ),
        );
      },
    );
  }

  Widget _buildInputBar(ChatProvider chat) {
    final voice = context.watch<VoiceProvider>();
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
                enabled: !chat.isStreaming,
                decoration: InputDecoration(
                  hintText: '输入消息...',
                  border: OutlineInputBorder(
                    borderRadius: BorderRadius.circular(24),
                    borderSide: BorderSide.none,
                  ),
                  filled: true,
                  fillColor: Theme.of(context).colorScheme.surfaceContainerHighest,
                  contentPadding: const EdgeInsets.symmetric(horizontal: 16, vertical: 10),
                ),
                maxLines: null,
                textInputAction: TextInputAction.send,
                onSubmitted: (_) => _sendMessage(chat),
              ),
            ),
            const SizedBox(width: 4),
            // 按住说话（系统识别与后端录音识别均不可用时给出引导）
            GestureDetector(
              onTapDown: chat.isStreaming || voice.isRecognizing
                  ? null
                  : (_) {
                      if (voice.initialized && !voice.voiceInputAvailable) {
                        _showVoiceUnavailable();
                        return;
                      }
                      _startVoice();
                    },
              onTapUp: (_) => _stopVoice(),
              onTapCancel: () => _stopVoice(),
              child: Container(
                padding: const EdgeInsets.all(10),
                decoration: BoxDecoration(
                  color: voice.isRecording
                      ? Theme.of(context).colorScheme.error
                      : Theme.of(context).colorScheme.surfaceContainerHighest,
                  shape: BoxShape.circle,
                ),
                child: Icon(
                  voice.isRecording
                      ? Icons.mic
                      : (voice.initialized && !voice.voiceInputAvailable
                          ? Icons.mic_off
                          : Icons.mic_none),
                  size: 20,
                  color: voice.isRecording
                      ? Colors.white
                      : (voice.initialized && !voice.voiceInputAvailable
                          ? Colors.grey
                          : Theme.of(context).colorScheme.onSurfaceVariant),
                ),
              ),
            ),
            const SizedBox(width: 4),
            IconButton(
              onPressed: chat.isStreaming ? null : () => _sendMessage(chat),
              icon: Icon(
                Icons.send,
                color: chat.isStreaming
                    ? Colors.grey
                    : Theme.of(context).colorScheme.primary,
              ),
            ),
          ],
        ),
      ),
    );
  }

  Widget _buildSettingsPanel(ChatProvider chat) {
    final voice = context.watch<VoiceProvider>();
    return AnimatedPositioned(
      duration: const Duration(milliseconds: 250),
      curve: Curves.easeOut,
      top: 0,
      bottom: 0,
      right: _settingsOpen ? 0 : -MediaQuery.of(context).size.width * 0.8,
      child: SizedBox(
        width: MediaQuery.of(context).size.width * 0.8,
        child: Material(
          color: Theme.of(context).colorScheme.surface,
          elevation: 8,
          child: SafeArea(
          child: ListView(
            padding: EdgeInsets.zero,
            children: [
              Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  mainAxisAlignment: MainAxisAlignment.spaceBetween,
                  children: [
                    const Text(
                      '设置',
                      style: TextStyle(fontSize: 20, fontWeight: FontWeight.bold),
                    ),
                    IconButton(
                      icon: const Icon(Icons.close),
                      onPressed: () => setState(() => _settingsOpen = false),
                    ),
                  ],
                ),
              ),
              const Divider(),
              ListTile(
                leading: const Icon(Icons.person),
                title: const Text('当前角色'),
                subtitle: Text(chat.characterName.isEmpty ? '未选择' : chat.characterName),
              ),
              ListTile(
                leading: const Icon(Icons.memory),
                title: const Text('模型'),
                subtitle: DropdownButton<String>(
                  value: chat.model ?? 'deepseek-flash',
                  isExpanded: true,
                  underline: const SizedBox.shrink(),
                  items: const [
                    DropdownMenuItem(
                      value: 'deepseek-flash',
                      child: Text('deepseek-flash（响应快）'),
                    ),
                    DropdownMenuItem(
                      value: 'deepseek-chat',
                      child: Text('deepseek-chat（更稳定）'),
                    ),
                  ],
                  onChanged: chat.setModel,
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      'Temperature  ${chat.temperature.toStringAsFixed(1)}',
                      style: const TextStyle(fontSize: 14),
                    ),
                    Slider(
                      value: chat.temperature,
                      min: 0.0,
                      max: 1.5,
                      divisions: 15,
                      label: chat.temperature.toStringAsFixed(1),
                      onChanged: chat.setTemperature,
                    ),
                  ],
                ),
              ),
              ListTile(
                leading: const Icon(Icons.record_voice_over),
                title: const Text('自动朗读回复'),
                subtitle: Text(
                  !voice.initialized
                      ? '正在检测语音能力…'
                      : !voice.ttsAvailable
                          ? '当前设备没有语音合成引擎，无法朗读'
                          : (voice.autoPlay
                              ? '开启，回复完自动念出来'
                              : '关闭，点气泡旁的小喇叭播放'),
                  style: const TextStyle(fontSize: 12),
                ),
                trailing: Switch(
                  value: voice.autoPlay,
                  onChanged: voice.setAutoPlay,
                ),
              ),
              Padding(
                padding: const EdgeInsets.symmetric(horizontal: 16),
                child: Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    Text(
                      '语速  ${voice.speechRate.toStringAsFixed(1)}',
                      style: const TextStyle(fontSize: 14),
                    ),
                    Slider(
                      value: voice.speechRate,
                      min: 0.3,
                      max: 1.0,
                      divisions: 14,
                      label: voice.speechRate.toStringAsFixed(1),
                      onChanged: voice.setSpeechRate,
                    ),
                  ],
                ),
              ),
              ListTile(
                leading: const Icon(Icons.delete_sweep),
                title: const Text('清空对话'),
                onTap: () {
                  chat.clearMessages();
                  setState(() => _settingsOpen = false);
                },
              ),
              ListTile(
                leading: const Icon(Icons.stop_circle),
                title: const Text('结束会话并记忆'),
                onTap: () async {
                  await chat.endSession();
                  setState(() => _settingsOpen = false);
                  if (mounted) {
                    ScaffoldMessenger.of(context).showSnackBar(
                      const SnackBar(content: Text('会话已结束，正在整理记忆...')),
                    );
                  }
                },
              ),
              const Padding(
                padding: EdgeInsets.all(16),
                child: Text(
                  '左滑关闭面板',
                  style: TextStyle(color: Colors.grey, fontSize: 12),
                ),
              ),
            ],
          ),
          ),
        ),
      ),
    );
  }

  void _sendMessage(ChatProvider chat) {
    final text = _controller.text.trim();
    if (text.isEmpty) return;
    final voice = context.read<VoiceProvider>();
    // 打断正在播放的语音，避免叠音
    voice.stopSpeaking();
    _controller.clear();
    chat.sendMessage(
      text,
      onComplete: (reply) {
        if (voice.autoPlay && voice.ttsAvailable) voice.speak(reply);
      },
    );
  }
}
