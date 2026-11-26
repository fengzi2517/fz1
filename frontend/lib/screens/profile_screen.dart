import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import '../config/api_config.dart';
import '../providers/app_settings_provider.dart';
import '../providers/voice_provider.dart';
import '../widgets/app_avatar.dart';

/// 我的：账号信息、语音设置与服务状态
class ProfileScreen extends StatelessWidget {
  const ProfileScreen({super.key});

  @override
  Widget build(BuildContext context) {
    final voice = context.watch<VoiceProvider>();
    final inputStatus = voice.sttAvailable
        ? '系统语音识别'
        : (voice.voiceInputAvailable ? '后端录音识别' : '不可用');
    final ttsStatus = voice.ttsAvailable ? '已启用' : '不可用';

    return Scaffold(
      appBar: AppBar(title: const Text('我的')),
      body: ListView(
        children: [
          const SizedBox(height: 16),
          Padding(
            padding: const EdgeInsets.symmetric(horizontal: 20),
            child: Row(
              children: [
                const AppAvatar(name: '我', size: 64),
                const SizedBox(width: 16),
                Column(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: [
                    const Text(
                      '我',
                      style: TextStyle(
                        fontSize: 20,
                        fontWeight: FontWeight.w600,
                      ),
                    ),
                    const SizedBox(height: 4),
                    Text(
                      ApiConfig.defaultUserId,
                      style: TextStyle(
                        fontSize: 12,
                        color: Colors.grey.shade500,
                      ),
                    ),
                  ],
                ),
              ],
            ),
          ),
          const SizedBox(height: 24),
          const _SectionTitle('外观'),
          const _ThemeColorTile(),
          const _TextScaleTile(),
          const _SectionTitle('语音'),
          SwitchListTile(
            title: const Text('自动朗读回复'),
            subtitle: const Text('角色回复后自动播放语音'),
            value: voice.autoPlay,
            onChanged: (v) => context.read<VoiceProvider>().setAutoPlay(v),
          ),
          ListTile(
            title: const Text('语速'),
            subtitle: Slider(
              value: voice.speechRate.clamp(0.3, 1.0),
              min: 0.3,
              max: 1.0,
              divisions: 14,
              label: voice.speechRate.toStringAsFixed(2),
              onChanged: (v) =>
                  context.read<VoiceProvider>().setSpeechRate(v),
            ),
            trailing: Text(
              voice.speechRate.toStringAsFixed(2),
              style: TextStyle(color: Colors.grey.shade600),
            ),
          ),
          const _SectionTitle('服务状态'),
          ListTile(
            leading: const Icon(Icons.mic_none),
            title: const Text('语音识别'),
            subtitle: Text(inputStatus),
            trailing: Icon(
              voice.voiceInputAvailable ? Icons.check_circle : Icons.error,
              color: voice.voiceInputAvailable ? Colors.green : Colors.grey,
            ),
          ),
          ListTile(
            leading: const Icon(Icons.volume_up_outlined),
            title: const Text('语音合成'),
            subtitle: Text(ttsStatus),
            trailing: Icon(
              voice.ttsAvailable ? Icons.check_circle : Icons.error,
              color: voice.ttsAvailable ? Colors.green : Colors.grey,
            ),
          ),
          const _SectionTitle('关于'),
          const ListTile(
            leading: Icon(Icons.info_outline),
            title: Text('AI 角色扮演'),
            subtitle: Text('v0.2.0 · 角色对话 / 群聊 / 语音'),
          ),
          const SizedBox(height: 24),
        ],
      ),
    );
  }
}

/// 主题色选择：点击即时生效并持久化
class _ThemeColorTile extends StatelessWidget {
  const _ThemeColorTile();

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<AppSettingsProvider>();
    return ListTile(
      title: const Text('主题色'),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 10),
        child: Wrap(
          spacing: 16,
          runSpacing: 10,
          children: [
            for (var i = 0; i < AppSettingsProvider.themeOptions.length; i++)
              _ThemeDot(
                option: AppSettingsProvider.themeOptions[i],
                selected: settings.themeIndex == i,
                onTap: () =>
                    context.read<AppSettingsProvider>().setThemeIndex(i),
              ),
          ],
        ),
      ),
    );
  }
}

class _ThemeDot extends StatelessWidget {
  final ThemeOption option;
  final bool selected;
  final VoidCallback onTap;

  const _ThemeDot({
    required this.option,
    required this.selected,
    required this.onTap,
  });

  @override
  Widget build(BuildContext context) {
    return Semantics(
      selected: selected,
      button: true,
      label: option.label,
      child: GestureDetector(
        onTap: onTap,
        child: Column(
          mainAxisSize: MainAxisSize.min,
          children: [
            Container(
              width: 34,
              height: 34,
              decoration: BoxDecoration(
                color: option.color,
                shape: BoxShape.circle,
                border: Border.all(
                  color: selected ? Colors.black87 : Colors.transparent,
                  width: 2.5,
                ),
              ),
              child: selected
                  ? const Icon(Icons.check, size: 18, color: Colors.white)
                  : null,
            ),
            const SizedBox(height: 4),
            Text(
              option.label,
              style: TextStyle(
                fontSize: 11,
                color: selected ? Colors.black87 : Colors.grey.shade600,
              ),
            ),
          ],
        ),
      ),
    );
  }
}

/// 聊天字号：小 / 标准 / 大
class _TextScaleTile extends StatelessWidget {
  const _TextScaleTile();

  static const List<(double, String)> _options = [
    (0.9, '小'),
    (1.0, '标准'),
    (1.15, '大'),
  ];

  @override
  Widget build(BuildContext context) {
    final settings = context.watch<AppSettingsProvider>();
    return ListTile(
      title: const Text('聊天字号'),
      subtitle: Padding(
        padding: const EdgeInsets.only(top: 8),
        child: Wrap(
          spacing: 8,
          children: [
            for (final (scale, label) in _options)
              ChoiceChip(
                label: Text(label),
                selected: (settings.textScale - scale).abs() < 0.01,
                onSelected: (_) =>
                    context.read<AppSettingsProvider>().setTextScale(scale),
              ),
          ],
        ),
      ),
    );
  }
}

class _SectionTitle extends StatelessWidget {
  final String text;

  const _SectionTitle(this.text);

  @override
  Widget build(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.fromLTRB(20, 16, 20, 4),
      child: Text(
        text,
        style: TextStyle(
          fontSize: 13,
          fontWeight: FontWeight.w600,
          color: Colors.grey.shade500,
        ),
      ),
    );
  }
}
