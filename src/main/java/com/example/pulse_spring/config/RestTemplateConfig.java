package com.example.pulse_spring.config;

import org.springframework.context.annotation.Bean;
import org.springframework.context.annotation.Configuration;
import org.springframework.context.annotation.Primary;
import org.springframework.http.client.SimpleClientHttpRequestFactory;
import org.springframework.web.client.RestTemplate;

/**
 * 외부 API 호출용 RestTemplate 빈 중앙 설정.
 * 이전에는 서비스마다 필드로 RestTemplate을 직접 생성해 타임아웃 설정이 제각각이거나
 * 누락(KakaoLocalClient)된 경우가 있었다. 여기서 한 번에 관리해 무한 대기(hang)를 막는다.
 */
@Configuration
public class RestTemplateConfig {

    // Kakao/날씨 등 일반 외부 API 호출용. 빠른(<1s) 응답이 정상이므로 짧은 타임아웃으로 스레드 점유를 막는다.
    @Primary
    @Bean
    public RestTemplate restTemplate() {
        return new RestTemplate(createRequestFactory(3000, 5000));
    }

    // FastAPI의 LLM 호출(리뷰 답글/상권 액션)은 수십 초가 걸릴 수 있어 read 타임아웃을 넉넉히 둔다.
    // 무한 행(hang)은 막되, 정상 LLM 지연은 허용한다.
    @Bean
    public RestTemplate fastApiRestTemplate() {
        return new RestTemplate(createRequestFactory(3000, 60000));
    }

    private SimpleClientHttpRequestFactory createRequestFactory(int connectTimeoutMs, int readTimeoutMs) {
        SimpleClientHttpRequestFactory factory = new SimpleClientHttpRequestFactory();
        factory.setConnectTimeout(connectTimeoutMs);
        factory.setReadTimeout(readTimeoutMs);
        return factory;
    }
}
