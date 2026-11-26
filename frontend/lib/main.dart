import 'package:flutter/material.dart';
import 'package:provider/provider.dart';

import 'providers/app_settings_provider.dart';
import 'providers/chat_provider.dart';
import 'providers/conversation_provider.dart';
import 'providers/group_provider.dart';
import 'providers/voice_provider.dart';
import 'screens/home_shell.dart';

void main() {
  runApp(const MyApp());
}

class MyApp extends StatelessWidget {
  const MyApp({super.key});

  @override
  Widget build(BuildContext context) {
    return MultiProvider(
      providers: [
        ChangeNotifierProvider(create: (_) => AppSettingsProvider()..init()),
        ChangeNotifierProvider(create: (_) => ChatProvider()),
        ChangeNotifierProvider(create: (_) => VoiceProvider()),
        ChangeNotifierProvider(create: (_) => GroupProvider()),
        ChangeNotifierProvider(create: (_) => ConversationProvider()),
      ],
      child: Consumer<AppSettingsProvider>(
        builder: (context, settings, _) {
          final seed = settings.seedColor;
          return MaterialApp(
            title: 'AI 角色扮演',
            theme: ThemeData(
              colorScheme: ColorScheme.fromSeed(
                seedColor: seed,
                brightness: Brightness.light,
              ),
              useMaterial3: true,
              scaffoldBackgroundColor: Colors.white,
            ),
            darkTheme: ThemeData(
              colorScheme: ColorScheme.fromSeed(
                seedColor: seed,
                brightness: Brightness.dark,
              ),
              useMaterial3: true,
            ),
            builder: (context, child) => MediaQuery.withClampedTextScaling(
              minScaleFactor: settings.textScale,
              maxScaleFactor: settings.textScale,
              child: child ?? const SizedBox.shrink(),
            ),
            home: const HomeShell(),
          );
        },
      ),
    );
  }
}
